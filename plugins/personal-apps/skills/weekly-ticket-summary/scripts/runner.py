"""osgen workflow runner, Python reference (plans/osgen/workflow.md). Runs a checked spec's workflows over a store:
load → map → validate → save, and ai → check → approve with waits. The agent plugins use it through `app`, osgen
uses it for golden; runtime/js/runner.ts is the same code for the h1902y OS. Steps never read the clock: `as_of`
comes with the trigger.
"""
from __future__ import annotations

import hashlib
import json
import re

import checks as K
import core as C
from expr import evaluate
from parse import parse_field
from table import map_columns, parse_csv, trim
from template import render as render_tpl
from views import Views, periods


def period_of(spec: dict, as_of: str, given: str | None) -> str:
    if given:
        return given
    unit = spec["period"]["unit"]
    default = spec["period"].get("default", "as_of")
    day = as_of[:10]
    if unit == "week":
        monday = C.week_of(day)
        return C.iso_date(C.ms(monday) - 7 * 86400000) if default == "last_full" else monday
    return day


class Run:
    def __init__(self, rt: "Runtime", workflow: str, trigger: str, as_of: str, payload: dict, by: str, row: dict | None = None):
        self.rt, self.workflow, self.trigger, self.as_of, self.payload, self.by = rt, workflow, trigger, as_of, payload, by
        self.out: dict[str, object] = {}
        self.state = "running"
        self.problems: list[str] = []
        self.load: dict | None = None
        self.waiting: dict | None = None
        # A resumed run (another process, after `app submit` or an approval) reopens its row instead of adding one.
        self.row = row or rt.store.add_sys("runs", {"workflow": workflow, "trigger": trigger, "load": None, "as_of": as_of,
                                                    "state": "running", "steps": {}, "error": None, "started": as_of,
                                                    "finished": None, "by": by})


class Runtime:
    def __init__(self, spec: dict, store):
        self.spec, self.store = spec, store

    # ---------- triggers ----------

    def trigger(self, workflow: str, on: str, *, as_of: str, payload: dict | None = None, by: str = "buyer",
                fill: dict | None = None) -> Run:
        """Start a run. `fill` maps an AI step id to its output (the golden run and `app submit`)."""
        wf = self.spec["workflows"][workflow]
        if on == "agent":
            # an app agent's `run` tool (agents.md §3): only a workflow the agent lists, and the run is by that agent
            if not agent_may_run(self.spec, by, workflow):
                raise ValueError(f"{workflow} has no agent trigger for {by}")
        elif not any(t["on"] == on for t in wf["trigger"]):
            raise ValueError(f"{workflow} has no {on} trigger")
        run = Run(self, workflow, on, as_of, payload or {}, by)
        if on not in ("upload", "sample", "pull"):
            run.load = self.current_load(payload.get("period") if payload else None)
        self._go(run, fill or {})
        run.after = []  # runs this one's `after` triggers started (A2: find actions right after the notes load)
        if run.state == "done":
            for name, other in self.spec["workflows"].items():
                if any(t["on"] == "after" and t.get("workflow") == workflow for t in other["trigger"]):
                    run.after.append(self.trigger(name, "after", as_of=as_of, payload={"period": run.load["period"] if run.load else None},
                                                  by=by, fill=fill))
        return run

    def resume(self, run_row: int) -> Run:
        """Run a waiting run again from its first step: views are pure and checks deterministic, so the steps before
        the wait give the same results, and the AI steps take what was submitted for them (store.pending_ai)."""
        row = next(r for r in self.store.sys("runs") if r["_row"] == run_row)
        if row.get("state") not in ("waiting_ai", "waiting_approval"):
            raise ValueError(f"run {run_row} isn't waiting (it's {row.get('state')})")
        if self.spec["workflows"][row["workflow"]]["steps"][0]["type"] == "load":
            raise ValueError("a load run doesn't wait; start a new one")
        run = Run(self, row["workflow"], row["trigger"], row["as_of"], {}, row["by"], row=row)
        run.load = next((l for l in self.store.sys("loads") if l["_row"] == row.get("load")), None) or self.current_load()
        self._go(run, {})
        return run

    def current_load(self, period: str | None = None) -> dict | None:
        ps = periods(self.store)
        if period:
            ps = [l for l in ps if l["period"] == period]
        return ps[0] if ps else None

    def views(self, load: dict | None = None, now: str | None = None) -> Views:
        load = load if load is not None else self.current_load()
        ps = periods(self.store)
        at = next((i for i, l in enumerate(ps) if load and l["_row"] == load["_row"]), -1)
        prev = ps[at + 1] if at >= 0 and at + 1 < len(ps) else None
        return Views(self.spec, self.store, {"load": load, "prev": prev, "now": now})

    # ---------- steps ----------

    def _go(self, run: Run, fill: dict) -> None:
        for step in self.spec["workflows"][run.workflow]["steps"]:
            if step["id"] in run.out:
                continue
            try:
                res = getattr(self, f"_step_{step['type']}")(run, step, fill)
            except StepProblem as e:
                run.state = "failed"
                run.problems = e.problems
                self.store.set_sys("runs", run.row, {"state": "failed", "error": "; ".join(e.problems), "finished": run.as_of})
                return
            if res is WAIT:
                self.store.set_sys("runs", run.row, {"state": run.state, "load": run.load["_row"] if run.load else None})
                return
            run.out[step["id"]] = res
        run.state = "done"
        self.store.set_sys("runs", run.row, {"state": "done", "load": run.load["_row"] if run.load else None,
                                             "finished": run.as_of})

    def _inputs(self, run: Run, step: dict) -> list:
        ins = step.get("in")
        ins = [] if ins is None else ins if isinstance(ins, list) else [ins]
        vs = self.views(run.load)
        return [vs.view(i[5:]) if i.startswith("view:") else run.out[i] for i in ins]

    def _step_load(self, run: Run, step: dict, fill):
        files = run.payload.get("files") or {}
        if not files:
            raise StepProblem(["no file"])
        period = period_of(self.spec, run.as_of, run.payload.get("period"))
        run.load = self.store.add_sys("loads", {
            "period": period, "kind": run.trigger if run.trigger != "upload" else run.payload.get("kind", "upload"),
            "files": list(files), "mapping": {}, "as_of": run.as_of, "rows_read": None, "rows_skipped": None,
            "columns_missing": [], "at": run.as_of})
        if step.get("files"):
            # A multi-file load: each file goes to one table, by the sample's own list, else by its name.
            by_table = {}
            sample_map = (self.spec.get("sample") or {}).get("files") or {}
            for name, text in files.items():
                table = sample_map.get(name) if run.trigger == "sample" else None
                if table is None:
                    table = next((t for k, t in step["files"].items() if k in C.norm_key(name)), None)
                if table is None:
                    raise StepProblem([f"{name}: can't tell which of {', '.join(step['files'])} it is (put the name in the file name)"])
                by_table[table] = self._raw(text)
            missing = [t for t in step["files"].values() if t not in by_table]
            if missing:
                raise StepProblem([f"missing the {', '.join(missing)} file"])
            return {"kind": "multi", "tables": by_table}
        name, text = next(iter(files.items()))
        if step["format"] == "text":
            text = text.replace("\r\n", "\n")
            body = text[:-1] if text.endswith("\n") else text  # a file's final newline doesn't start a line
            return {"kind": "text", "lines": [{"line": str(i + 1), "text": t} for i, t in enumerate(body.split("\n"))]}
        return self._raw(text)

    @staticmethod
    def _raw(text: str) -> dict:
        rows = parse_csv(text)
        if not rows:
            return {"kind": "raw", "headers": [], "rows": [], "skipped": 0}
        headers = [trim(h) for h in rows[0]]
        body, skipped = [], 0
        for i, r in enumerate(rows[1:]):
            while len(r) > len(headers) and not trim(r[-1]):
                r = r[:-1]
            if len(r) != len(headers):
                skipped += 1
                continue
            body.append({"line": i + 2, "cells": r})
        return {"kind": "raw", "headers": headers, "rows": body, "skipped": skipped}

    def _step_map(self, run: Run, step: dict, fill):
        raw = self._inputs(run, step)[0]
        if raw.get("kind") == "multi":
            raw = raw["tables"][step["table"]]
        table = self.spec["tables"][step["table"]]
        overrides = (run.payload.get("mapping") or {}).get(step["table"], {})
        fields = [{"id": f, "match": [C.norm_key(a) for a in (d.get("aliases") or [f])]} for f, d in table["fields"].items()]
        mapping = map_columns(raw["headers"], fields, overrides)
        loads_mapping = dict(run.load.get("mapping") or {})
        loads_mapping[step["table"]] = mapping
        headers = dict(run.load.get("headers") or {})
        headers[step["table"]] = raw["headers"]  # the mapping component offers these columns
        self.store.set_sys("loads", run.load, {"mapping": loads_mapping, "headers": headers})
        return {**raw, "table": step["table"], "mapping": mapping}

    def _step_validate(self, run: Run, step: dict, fill):
        raw = self._inputs(run, step)[0]  # later inputs (a save it must follow) only order the steps
        tname = raw["table"]
        table = self.spec["tables"][tname]
        mapping = raw["mapping"]
        at = {f: raw["headers"].index(h) for f, h in mapping.items()}
        req = step.get("require_any") or []
        rows, skipped, problems = [], raw.get("skipped", 0), 0
        # refs resolve by the target's key, in the same load (or an app table): key -> _row
        refs = {}
        for f, d in table["fields"].items():
            if d["type"] == "ref":
                tgt = self.spec["tables"][d["to"]]
                refs[f] = {str(x.get(tgt["key"])): x["_row"] for x in self.store.rows(d["to"], None if tgt["scope"] == "app" else run.load)}
        bad_refs = 0
        for r in raw["rows"]:
            cells = r["cells"]
            row = {}
            for f, d in table["fields"].items():
                v, ok = parse_field(d, cells[at[f]]) if f in at else (None, True)
                if not ok:
                    problems += 1
                if f in refs and v is not None:
                    v = refs[f].get(v)
                    if v is None:
                        bad_refs += 1
                row[f] = v
            if req and all(row.get(f) in (None, "") for f in req):
                skipped += 1
                continue
            if any(d.get("required") and row.get(f) is None for f, d in table["fields"].items() if f != table["key"] or not step.get("key_fallback")):
                skipped += 1
                continue
            if row.get(table["key"]) in (None, "") and step.get("key_fallback"):
                row[table["key"]] = step["key_fallback"].replace("{line}", str(r["line"]))
            rows.append(row)
        missing = [g[0] if len(g) == 1 else " or ".join(g) for g in (step.get("needs") or []) if not any(f in mapping for f in g)]
        self._count(run, tname, len(rows), skipped, missing, problems + bad_refs)
        return {"kind": "rows", "table": tname, "rows": rows}

    def _count(self, run: Run, table: str, read: int, skipped: int, missing: list, problems: int) -> None:
        """A load's row counts (D-23): `per_table`, and totals over its tables. With several tables (validate steps) a
        missing column is named `table.field`."""
        per = dict(run.load.get("per_table") or {})
        per[table] = {"rows_read": str(read), "rows_skipped": str(skipped), "columns_missing": missing,
                      "cell_problems": str(problems)}
        several = sum(1 for s in self.spec["workflows"][run.workflow]["steps"] if s["type"] == "validate") > 1
        total = lambda k: str(sum(int(p[k]) for p in per.values()))
        self.store.set_sys("loads", run.load, {
            "rows_read": total("rows_read"), "rows_skipped": total("rows_skipped"), "cell_problems": total("cell_problems"),
            "columns_missing": [" or ".join(f"{t}.{f}" for f in m.split(" or ")) if several else m
                                for t, p in per.items() for m in p["columns_missing"]],
            "per_table": per})

    def _step_save(self, run: Run, step: dict, fill):
        src = self._inputs(run, step)[0]
        tname = step["table"]
        if isinstance(src, dict) and src.get("kind") == "text":
            rows = self._fields(run, step, src["lines"])
            if run.load and run.load.get("rows_read") is None:  # a text load has no validate step: its lines are its rows
                self._count(run, tname, len(rows), 0, [], 0)
        elif isinstance(src, dict) and src.get("kind") == "rows":
            rows = src["rows"]
        elif step.get("fields"):  # other rows (A3: approved reminder drafts): each field from its expression
            rows = self._fields(run, step, src)
        else:
            rows = [{f: r.get(f) for f in self.spec["tables"][tname]["fields"]} for r in src]
        if self._held(run, step):
            # a run an app agent started holds every save for approval, as if an approve step stood before it
            # (workflow.md §6): an approvals row with the exact rows, and the run waits
            decided = self.store.approval_for(run.row["_row"], step["id"])
            if decided is None:
                n = len(rows)
                self.store.add_sys("approvals", {"run": run.row["_row"], "step": step["id"], "writes": rows,
                                                 "summary": f"Save {n} {'row' if n == 1 else 'rows'} to {tname} (run by app agent {run.by[6:]})",
                                                 "state": "pending", "decided_at": None, "error": None})
                run.state = "waiting_approval"
                return WAIT
            if decided == "pending":
                run.state = "waiting_approval"
                return WAIT
            if decided != "applied":
                return None  # a no: this save writes nothing
        mode = step.get("mode", "replace_load")
        self.store.save_rows(tname, rows, run.load if self.spec["tables"][tname]["scope"] == "load" else None, mode,
                             self.spec["tables"][tname]["key"])
        return None

    def _held(self, run: Run, step: dict) -> bool:
        """An agent's run holds a save, unless its rows come straight from an approve step (already a yes)."""
        if not str(run.by or "").startswith("agent:"):
            return False
        ins = step.get("in")
        first = ins[0] if isinstance(ins, list) else ins
        src = next((s for s in self.spec["workflows"][run.workflow]["steps"] if s["id"] == first), None)
        return not (src and src["type"] == "approve")

    def _fields(self, run: Run, step: dict, src: list) -> list[dict]:
        """A save's `fields` over its input rows; names read the row, then load.* and prev.*, as in views (osgen
        types them against that env)."""
        vs = self.views(run.load)
        return [{f: evaluate(tree, vs._scope(r)) for f, tree in step["fields"].items()} for r in src]

    def _step_ai(self, run: Run, step: dict, fill):
        key = f"{run.workflow}.{step['id']}"
        value = fill[key] if key in fill else self.store.pending_ai(run.row["_row"], step["id"])
        if value is not None:
            return {"value": K.coerce(K.task_shape(self.spec, step), value), "filled_by": "agent"}
        run.state = "waiting_ai"
        # a check that sent this step back keeps its problems in the store: a resumed run shows them again
        problems = self.store.problems(run.row["_row"], step["id"])
        run.waiting = {"step": step["id"], "task": self.task_file(run, step, problems)}
        if problems:
            run.waiting["problems"] = problems
        return WAIT

    def _step_check(self, run: Run, step: dict, fill):
        ai_step = next(s for s in self.spec["workflows"][run.workflow]["steps"] if s["id"] == step["in"])
        got = run.out[step["in"]]
        value = got["value"]
        vs = self.views(run.load)
        inputs = self._inputs(run, ai_step)
        problems = K.run_checks(self.spec, ai_step, step, value, inputs, vs)
        tries = self.store.tries(run.row["_row"], step["id"])
        filled_by, state = got["filled_by"], ("retried" if tries else "passed")  # D-33
        if problems:
            if tries < step.get("retry", 1):
                self.store.set_tries(run.row["_row"], step["id"], tries + 1)
                self.store.set_problems(run.row["_row"], ai_step["id"], problems)
                self.store.clear_pending_ai(run.row["_row"], ai_step["id"])
                run.out.pop(ai_step["id"], None)
                run.state = "waiting_ai"
                run.waiting = {"step": ai_step["id"], "task": self.task_file(run, ai_step, problems), "problems": problems}
                return WAIT
            fb = step.get("fallback", "empty")
            state = "fallback"
            if fb.startswith("view:"):
                value, filled_by = vs.view(fb[5:]), "fallback"
            elif fb == "keep_valid":
                value = K.keep_valid(self.spec, ai_step, step, value, inputs, vs)
                state = "retried" if tries else "passed" if not problems else "fallback"
            elif fb == "empty":
                value = []
            else:
                raise StepProblem(problems)
        self.store.set_problems(run.row["_row"], ai_step["id"], [])  # the step is through: passed, or its fallback
        rows = value if isinstance(value, list) else [value]
        self.store.add_ai_output(run, ai_step, rows, filled_by, state, problems)
        return rows

    def _step_approve(self, run: Run, step: dict, fill):
        rows = self._inputs(run, step)[0]
        decided = self.store.approval_for(run.row["_row"], step["id"])
        if decided is None:
            self.store.add_sys("approvals", {"run": run.row["_row"], "step": step["id"], "writes": rows,
                                             "summary": render_tpl(step.get("summary", ""), {"count": str(len(rows))}), "state": "pending", "decided_at": None,
                                             "error": None})
            run.state = "waiting_approval"
            return WAIT
        if decided == "pending":
            run.state = "waiting_approval"
            return WAIT
        return rows if decided == "applied" else []

    def _step_push(self, run: Run, step: dict, fill):
        raise StepProblem(["push has no runtime in v1"])

    # ---------- the task file an agent fills ----------

    def task_file(self, run: Run, step: dict, problems: list | None = None) -> dict:
        inputs = self._inputs(run, step)
        ins = step["in"] if isinstance(step["in"], list) else [step["in"]]
        shown = []
        pct = K.pct_fields(self.spec)
        for ref, i in zip(ins, inputs):
            if isinstance(i, list):
                # money and percents as people read them (4,900.00 USD, 20%): the filler writes what it sees
                out = (self.spec["views"][ref[5:]].get("out") or {}) if ref.startswith("view:") else {}
                shown.append([{k: K.as_read(self.spec, out, k, v, pct) for k, v in r.items() if not k.startswith("_")} for r in i])
            else:
                shown.append(i)
        body = {"workflow": run.workflow, "step": step["id"], "task": step["task"], "instructions": step["instructions"],
                "rules": K.task_rules(self.spec, self.spec["workflows"][run.workflow]["steps"], step),
                "params": {k: v for k, v in step.items() if k not in ("id", "type", "in", "task", "instructions")},
                "shape": K.task_shape(self.spec, step), "inputs": shown, "problems": list(problems or [])}
        body["input_hash"] = hashlib.sha256(json.dumps(shown, sort_keys=True).encode()).hexdigest()[:16]
        return body


def agent_may_run(spec: dict, by: str, workflow: str) -> bool:
    """`by` is "agent:<name>" and that app agent holds {"run": workflow}."""
    if not str(by or "").startswith("agent:"):
        return False
    ag = (spec.get("agents") or {}).get(by[6:])
    return bool(ag) and any(t.get("run") == workflow for t in ag.get("tools") or [])


class StepProblem(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


WAIT = object()
