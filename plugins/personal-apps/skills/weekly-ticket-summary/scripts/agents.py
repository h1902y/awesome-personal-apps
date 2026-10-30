"""osgen app-agent tools (plans/osgen/agents.md §3–§5), Python reference; runtime/js/agents.ts is the same code.

One method set every app format exposes: the agent plugins through `app.py <command> --agent <name>`, the h1902y OS
through `env.APP` (its store calls the JS port). Every method first checks the calling agent's allowlist (its
`reads` and `tools`) and refuses in plain words (`Refused`); the hard line is still approvals: runs an agent starts
hold their saves, `propose` only creates an approvals row, `fill` writes only drafts (ai_outputs), and handoff and
memory notes are checked with numbers_backed against the agent's reads.

    tools = AgentTools(spec, rt, store, period=None, now=as_of, remap=callback)
    tools.query(agent, source, {fields, where: [{field, op, value}], sort, limit, period})
    tools.fill(agent, "wf.step", value, run=None)      -> Run      (the submit/resume path: the step's check runs)
    tools.run(agent, workflow, period=None)            -> Run      (trigger "agent", by "agent:<name>")
    tools.propose(agent, "mark" | "remap", table, payload)        (an approvals row; applied on yes by decide)
    tools.decide(approval, yes)                                   (a proposal's yes/no: marks, or the remap callback)
    tools.open(agent, page, focus=None)                -> {page, focus, markdown}
    tools.remember(agent, key, text) -> the note;  tools.recall(agent[, key]) -> the agent's own notes (D-30)
    tools.handoff(agent, to, {period, keys, note, hops})  -> the typed note
"""
from __future__ import annotations

import re

import checks as K
import core as C
import dec as D
from render import Renderer
from views import _cmp_typed, _sort, periods

OPS = ("eq", "ne", "lt", "le", "gt", "ge", "in", "contains", "is_null")
LIMIT = 200
MAX_KEYS = 50
MAX_NOTE = 300
MAX_MEMORY = 1000
MAX_HOPS = 4
NEVER = ("sys.connections", "sys.agent_memory")
NUMERIC = {"int", "number", "hours", "money"}
# the system tables' fields as agents see them (plans/osgen/tables.md §3; compile.py SYS_TYPES)
SYS_FIELDS = {
    "loads": {"period": "text", "kind": "text", "files": "list", "mapping": "any", "as_of": "datetime", "rows_read": "int",
              "rows_skipped": "int", "columns_missing": "list", "cell_problems": "int", "per_table": "any", "headers": "any", "at": "datetime"},
    "runs": {"workflow": "text", "trigger": "text", "load": "int", "as_of": "datetime", "state": "text", "steps": "any",
             "error": "text", "started": "datetime", "finished": "datetime", "by": "text"},
    "ai_outputs": {"run": "int", "workflow": "text", "step": "text", "task": "text", "load": "int", "value": "list",
                   "filled_by": "text", "check": "text", "problems": "list", "at": "datetime"},
    "approvals": {"run": "int", "step": "text", "writes": "any", "summary": "text", "state": "text", "decided_at": "datetime",
                  "error": "text", "by": "text"},
    "marks": {"table": "text", "key": "text", "period": "text", "mark": "text", "until": "datetime", "at": "datetime"},
    "schedules": {"workflow": "text", "rule": "text", "timezone": "text", "on": "bool", "claimed_by": "text",
                  "last_fired": "datetime", "next": "datetime"},
}


class Refused(Exception):
    """A tool call the agent may not make, in plain words (the same text in every runtime)."""


def _norm(v):
    """A value an agent gave, as the runtime's canonical value: numbers as decimal strings, the rest as is."""
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        d = D.from_(v)
        return d if d is not None else str(v)
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v


def _minor(spec: dict, f: str, v):
    """An agent's money value (major units, as people write it) in the store's minor units (D-20)."""
    n = _num(v)
    if n is None:
        return v
    return n if (K.money_currency(spec, f) or "").upper() in C.ZERO_DECIMAL else D.mul(n, "100")


def _num(v):
    """A decimal string from a number or a numeric string; None for anything else (bools, lists, text)."""
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    return D.from_(v)


def _sortable(v, t: str):
    if v is None or isinstance(v, (list, dict)):
        return None
    if t in NUMERIC:
        return _num(v)
    if t == "bool":
        return v if isinstance(v, bool) else None
    return _key(v)


def _key(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return D.from_(v) or str(v)
    return v if isinstance(v, str) else "?"


def _len(s: str) -> int:
    return len(s)  # code points (JS: cpLen)


def _int(v):
    """A whole number (an integral JSON number), else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v == v and v not in (float("inf"), float("-inf")) and v.is_integer():
        return int(v)
    return None


class AgentTools:
    def __init__(self, spec: dict, rt, store, period: str | None = None, now: str | None = None, remap=None):
        self.spec, self.rt, self.store = spec, rt, store
        self.period, self.now, self.remap = period, now, remap

    # ---------- the allowlist ----------

    def _agent(self, name: str) -> dict:
        agents = self.spec.get("agents") or {}
        if name not in agents:
            raise Refused(f"there's no app agent {name}" + (f" (agents: {', '.join(agents)})" if agents else ": this app has none"))
        return agents[name]

    @staticmethod
    def _tools(ag: dict, kind: str) -> list:
        return [t for t in ag.get("tools") or [] if kind in t]

    def _holds(self, name: str, ag: dict, kind: str, value: str, what: str) -> dict:
        hits = [t for t in self._tools(ag, kind) if t[kind] == value]
        if not hits:
            have = [t[kind] for t in self._tools(ag, kind)]
            raise Refused(f"{name} can't {what} {value}" + (f": it can {what} {', '.join(have)}" if have else f": it has no {kind} tool"))
        return hits[0]

    def _load(self, period):
        """The load of `period` (or the period shown, else the latest); None when there's no data yet."""
        if period is not None:
            if not isinstance(period, str):
                raise Refused("period is a period's name")
            load = self.rt.current_load(period)
            if load is None:
                raise Refused(f"there's no data for {period}")
            return load
        return (self.rt.current_load(self.period) if self.period is not None else None) or self.rt.current_load(None)

    # ---------- read: query ----------

    def _types(self, source: str) -> dict:
        if source.startswith("view:"):
            return self.spec["views"][source[5:]].get("out") or {}
        if source.startswith("sys."):
            return SYS_FIELDS.get(source[4:], {})
        return {f: d["type"] for f, d in self.spec["tables"][source]["fields"].items()}

    def _rows(self, source: str, load) -> list:
        if source.startswith("view:"):
            return self.rt.views(load, self.now).view(source[5:])
        if source.startswith("sys."):
            return self.store.sys(source[4:])
        scope = self.spec["tables"][source]["scope"]
        return self.store.rows(source, None if scope == "app" else load)

    def _readable(self, name: str, ag: dict, source: str) -> None:
        reads = ag.get("reads") or []
        if source in NEVER or source not in reads:
            raise Refused(f"{name} can't read {source}: it reads {', '.join(reads)}")
        ok = (source.startswith("view:") and source[5:] in self.spec["views"]) or \
             (source.startswith("sys.") and source[4:] in SYS_FIELDS) or source in self.spec["tables"]
        if not ok:
            raise Refused(f"{source} names nothing in this app")

    def query(self, agent: str, source: str, opts: dict | None = None) -> dict:
        ag = self._agent(agent)
        if isinstance(source, str) and ":" not in source and not source.startswith("sys.") and source in self.spec["views"]:
            source = f"view:{source}"  # a bare name is a view first, then a table (agents.md §2)
        self._readable(agent, ag, source)
        opts = opts if opts is not None else {}
        if not isinstance(opts, dict):
            raise Refused("the query options are {fields, where, sort, limit, period}")
        period = opts.get("period")
        if period == "all":
            if ag.get("memory") != "app":
                raise Refused(f"{agent} reads one period at a time")
        elif period is not None and not isinstance(period, str):
            raise Refused("period is a period's name, or all")
        types = dict(self._types(source))
        if period == "all":
            types.setdefault("period", "text")
        names = ", ".join(types)
        fields = opts.get("fields")
        if fields is not None:
            if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
                raise Refused("fields is a list of field names")
            for f in fields:
                if f not in types:
                    raise Refused(f"{source} has no field {f} (fields: {names})")
        where = opts.get("where") or []
        if isinstance(where, dict):
            where = [where]
        if not isinstance(where, list):
            raise Refused("where is a list of {field, op, value}")
        conds = []
        for w in where:
            if not isinstance(w, dict) or not isinstance(w.get("field"), str):
                raise Refused("each where is {field, op, value}")
            f, op = w["field"], w.get("op")
            if op is None:
                op = "eq"
            if f not in types:
                raise Refused(f"{source} has no field {f} (fields: {names})")
            if not isinstance(op, str) or op not in OPS:
                raise Refused(f"{op if isinstance(op, str) else 'that'} isn't an op (ops: {', '.join(OPS)})")
            value = _norm(w.get("value"))
            if op == "in" and not isinstance(value, list):
                raise Refused(f"{f} in: the value is a list")
            if types[f] in NUMERIC and op in ("eq", "ne", "lt", "le", "gt", "ge") and value is not None and _num(value) is None:
                raise Refused(f"{f} is a number: compare it with a number")
            if types[f] == "money":  # agents speak major units (4900 = 4,900.00 USD); the store keeps minor
                value = [_minor(self.spec, f, e) for e in value] if op == "in" else _minor(self.spec, f, value)
            conds.append((f, op, value))
        sort = opts.get("sort") or []
        if isinstance(sort, (str, dict)):
            sort = [sort]
        if not isinstance(sort, list):
            raise Refused(f"sort by a field of {source} (fields: {names})")
        keys = []
        for k in sort:
            if isinstance(k, str):
                k = {"by": k[1:], "dir": "desc"} if k.startswith("-") else {"by": k, "dir": "asc"}
            if not isinstance(k, dict) or not isinstance(k.get("by"), str) or k["by"] not in types:
                raise Refused(f"sort by a field of {source} (fields: {names})")
            d = k.get("dir")
            if d is None:
                d = "asc"
            if d not in ("asc", "desc"):
                raise Refused("sort dir is asc or desc")
            keys.append({"by": k["by"], "dir": d})
        limit = opts.get("limit")
        if limit is None:
            limit = LIMIT
        limit = _int(limit)
        if limit is None or limit < 0:
            raise Refused("limit is a whole number from 0 to 200")
        limit = min(limit, LIMIT)
        if period == "all" and not source.startswith("sys."):
            loads = [(l, l["period"]) for l in periods(self.store)]
        else:
            load = None if source.startswith("sys.") else self._load(period if period != "all" else None)
            loads = [(load, None)]
        rows = []
        for load, tag in loads:
            for r in self._rows(source, load):
                r = {**r, "period": tag} if tag is not None else r
                if all(_match(r.get(f), op, v, types[f]) for f, op, v in conds):
                    rows.append(r)
        if keys:
            shadow = []
            for i, r in enumerate(rows):
                sh = {"_i": i, **({"_row": r["_row"]} if "_row" in r else {})}
                for k in keys:
                    sh[k["by"]] = _sortable(r.get(k["by"]), types.get(k["by"], "text"))
                shadow.append(sh)
            rows = [rows[sh["_i"]] for sh in _sort(shadow, keys, lambda f: types.get(f, "text"))]
        total = len(rows)
        money = lambda f, v: C.fmt_money(v, K.money_currency(self.spec, f)) if types.get(f) == "money" and isinstance(v, str) and D.is_dec(v) else v
        out = []
        for r in rows[:limit]:  # money as people read it (4,900.00 USD)
            out.append({f: money(f, r.get(f)) for f in fields} if fields is not None else
                       {f: money(f, v) for f, v in r.items() if not f.startswith("_")})
        if period == "all":
            shown = "all"
        elif source.startswith("sys."):
            shown = None
        else:
            shown = loads[0][0]["period"] if loads[0][0] else None
        return {"source": source, "period": shown, "total": total, "rows": out}

    # ---------- write paths: fill, run, propose ----------

    def fill(self, agent: str, target: str, value, run: int | None = None):
        ag = self._agent(agent)
        self._holds(agent, ag, "fill", target, "fill")
        wf, _, step = target.partition(".")
        st = next((s for s in (self.spec["workflows"].get(wf) or {}).get("steps") or [] if s["id"] == step), None)
        if st is None or st["type"] != "ai":
            raise Refused(f"{target} isn't an AI step")
        runs = [r for r in self.store.sys("runs") if r.get("workflow") == wf and r.get("state") == "waiting_ai"]
        if run is not None:
            runs = [r for r in runs if r["_row"] == run]
            if not runs:
                raise Refused(f"run {run} isn't a {wf} run waiting for an AI step")
        else:
            load = self._load(None)
            here = [r for r in runs if load and r.get("load") == load["_row"]]
            runs = here or runs
            if not runs:
                raise Refused(f"no {wf} run is waiting for {step}: start one first")
        row = runs[-1]["_row"]
        self.store.submit_ai(row, step, value)
        return self.rt.resume(row)

    def run(self, agent: str, workflow: str, period: str | None = None):
        ag = self._agent(agent)
        self._holds(agent, ag, "run", workflow, "run")
        wf = self.spec["workflows"].get(workflow)
        if wf is None:
            raise Refused(f"there's no workflow {workflow}")
        if wf["steps"][0]["type"] == "load":
            raise Refused(f"{workflow} loads a file: the user loads files, an app agent can't")
        if self.now is None:
            raise Refused("no clock: the app gives every run its as_of")
        load = self._load(period)
        if load is None:
            raise Refused("there's no data yet: the user loads a file first")
        return self.rt.trigger(workflow, "agent", as_of=self.now, payload={"period": load["period"] if load else None},
                               by=f"agent:{agent}")

    def propose(self, agent: str, kind: str, table: str, payload: dict | None = None) -> dict:
        ag = self._agent(agent)
        if kind not in ("mark", "remap"):
            raise Refused(f"{kind} isn't a proposal (mark or remap)")
        self._holds(agent, ag, kind, table, kind)
        if table not in self.spec["tables"]:
            raise Refused(f"there's no table {table}")
        payload = payload or {}
        if not isinstance(payload, dict):
            raise Refused("the proposal is an object")
        load = self._load(payload.get("period"))
        if load is None:
            raise Refused("there's no data yet")
        t = self.spec["tables"][table]
        if kind == "mark":
            keys = payload.get("keys")
            if keys is None and payload.get("key") is not None:
                keys = [payload["key"]]
            keys = self._keys(keys)
            if not keys:
                raise Refused("which rows? give their keys")
            mark = payload.get("mark")
            if mark not in ("done", "snoozed", "open"):
                raise Refused("mark is done, snoozed or open")
            have = {_key(r.get(t["key"])) for r in self.store.rows(table, None if t["scope"] == "app" else load)}
            for k in keys:
                if k not in have:
                    raise Refused(f"{k} isn't a {table} key in the {load['period']} data")
            until = None
            span = payload.get("for", "1d") if mark == "snoozed" else None
            if span is not None:
                m = re.fullmatch(r"([1-9][0-9]{0,2})([dh])", str(span)) if isinstance(span, str) else None
                if not m:
                    raise Refused("snooze for is a number of days or hours, like 1d or 12h")
                if self.now is None:
                    raise Refused("no clock: the app gives every proposal its time")
                until = C.iso_datetime(C.ms(self.now) + int(m.group(1)) * (86400000 if m.group(2) == "d" else 3600000))
            writes = {"op": "mark", "table": table, "period": load["period"],
                      "rows": [{"key": k, "mark": mark, "until": until} for k in keys]}
            summary = f"Mark {', '.join(keys)} {mark}" + (f" until {until[:16].replace('T', ' ')} UTC" if until else "") + f" (proposed by {agent})"
        else:
            mapping = payload.get("mapping")
            if not isinstance(mapping, dict) or not mapping:
                raise Refused("mapping is {field: column}")
            cols = ((load.get("headers") or {}).get(table)) or []
            for f, col in mapping.items():
                if f not in t["fields"]:
                    raise Refused(f"{table} has no field {f} (fields: {', '.join(t['fields'])})")
                if not isinstance(col, str):
                    raise Refused("each mapping is a field and a column name")
                if cols and col not in cols:
                    raise Refused(f"the file has no column {col} (columns: {', '.join(cols)})")
            writes = {"op": "remap", "table": table, "period": load["period"], "mapping": dict(mapping)}
            summary = f"Read {table} with " + ", ".join(f"{f} from {c}" for f, c in mapping.items()) + f" (proposed by {agent})"
        a = self.store.add_sys("approvals", {"run": None, "step": None, "writes": writes, "summary": summary, "state": "pending",
                                             "decided_at": None, "error": None, "by": f"agent:{agent}"})
        return {"approval": a["_row"], "state": "pending", "summary": summary}

    def decide(self, approval: int, yes: bool) -> dict:
        """The user's yes or no on an agent's proposal (an approvals row with no run)."""
        a = next((x for x in self.store.sys("approvals") if x["_row"] == approval), None)
        if a is None or a.get("state") != "pending":
            raise Refused("that approval isn't waiting any more")
        w = a.get("writes")
        if a.get("run") is not None or not isinstance(w, dict) or w.get("op") not in ("mark", "remap"):
            raise Refused("that approval belongs to a run: decide it on the run")
        if not yes:
            self.store.set_sys("approvals", a, {"state": "rejected", "decided_at": self.now})
            return {"approval": approval, "state": "rejected", "message": "Nothing changed."}
        try:
            if w["op"] == "mark":
                for r in w["rows"]:
                    self.store.set_mark(w["table"], r["key"], w["period"], None if r["mark"] == "open" else r["mark"], r["until"], self.now)
                msg = f"Marked {len(w['rows'])}."
            else:
                if self.remap is None:
                    raise Refused("this app can't change a mapping here")
                msg = self.remap(w["table"], w["mapping"]) or "Mapping saved."
        except Exception as e:  # noqa: BLE001
            self.store.set_sys("approvals", a, {"state": "failed", "decided_at": self.now, "error": str(e)})
            raise
        self.store.set_sys("approvals", a, {"state": "applied", "decided_at": self.now})
        return {"approval": approval, "state": "applied", "message": msg}

    # ---------- open, remember, handoff ----------

    def open(self, agent: str, page: str, focus: str | None = None) -> dict:
        ag = self._agent(agent)
        tool = self._holds(agent, ag, "open", page, "open")
        pg = next((p for p in self.spec["pages"] if p["id"] == page), None)
        if pg is None:
            raise Refused(f"there's no page {page}")
        focus = focus if focus is not None else tool.get("focus")
        comp = None
        if focus is not None:
            comp = next((c for c in pg["components"] + (pg.get("empty") or []) if c["id"] == focus), None)
            if comp is None:
                raise Refused(f"{page} has no component {focus}")
        load = self._load(None)
        md = None
        if load is not None:
            r = Renderer(self.spec, self.rt.views(load, self.now))
            md = r.component(comp) if comp else r.page(page)
        return {"page": page, "focus": focus, "markdown": md}

    def _scope(self, agent: str, ag: dict):
        mem = ag.get("memory") or "none"
        if mem == "none":
            raise Refused(f"{agent} keeps no notes (memory none)")
        if mem == "app":
            return None
        load = self._load(None)
        if load is None:
            raise Refused(f"{agent} keeps notes per period, and there's no data yet")
        return load["period"]

    def _mine(self, agent: str, ag: dict) -> tuple[str | None, list[dict]]:
        period = self._scope(agent, ag)
        return period, [m for m in self.store.sys("agent_memory") if m.get("agent") == agent and m.get("period") == period]

    def recall(self, agent: str, key: str | None = None) -> list[dict]:
        """The agent's own notes (D-30): the current period's (`period` memory) or all of them (`app`); `key` picks one."""
        _, mine = self._mine(agent, self._agent(agent))
        return [{"key": m["key"], "text": m["text"], "period": m["period"], "at": m["at"]} for m in mine if key is None or m["key"] == key]

    def remember(self, agent: str, key: str | None = None, text: str | None = None) -> dict:
        ag = self._agent(agent)
        period, mine = self._mine(agent, ag)
        if text is None:
            raise Refused("remember keeps a note: give its key and text (recall reads your notes)")
        if not isinstance(key, str) or not key.strip() or _len(key) > 80:
            raise Refused("a note's key is a short name (at most 80 characters)")
        if not isinstance(text, str) or _len(text) > MAX_MEMORY:
            raise Refused(f"a note is text of at most {MAX_MEMORY:,} characters")
        self._backed(agent, ag, text, "note")
        hit = next((m for m in mine if m["key"] == key), None)
        if hit:
            self.store.set_sys("agent_memory", hit, {"text": text, "at": self.now})
        else:
            hit = self.store.add_sys("agent_memory", {"agent": agent, "period": period, "key": key, "text": text, "at": self.now})
        return {"key": key, "text": text, "period": period, "at": hit["at"]}

    def handoff(self, agent: str, to: str, note: dict | None = None) -> dict:
        ag = self._agent(agent)
        self._holds(agent, ag, "handoff", to, "hand off to")
        self._agent(to)
        note = note or {}
        if not isinstance(note, dict):
            raise Refused("a handoff note is {period, keys, note}")
        hops = _int(note.get("hops", 0))
        if hops is None or hops < 0:
            raise Refused("hops is a whole number: the hops of the note you were given")
        if hops >= MAX_HOPS:
            raise Refused(f"this conversation has handed off {MAX_HOPS} times already: answer the user yourself")
        keys = self._keys(note.get("keys"))
        text = note.get("note", "")
        if not isinstance(text, str) or _len(text) > MAX_NOTE:
            raise Refused(f"a handoff note is text of at most {MAX_NOTE} characters")
        load = self._load(note.get("period"))
        self._backed(agent, ag, text, "handoff note")
        return {"from": agent, "to": to, "period": load["period"] if load else None, "keys": keys, "note": text, "hops": hops + 1}

    # ---------- helpers ----------

    def _keys(self, keys) -> list[str]:
        if keys is None:
            return []
        if not isinstance(keys, list):
            raise Refused("keys is a list")
        if len(keys) > MAX_KEYS:
            raise Refused(f"at most {MAX_KEYS} keys")
        out = []
        for k in keys:
            if isinstance(k, bool) or k is None or not isinstance(k, (str, int, float)):
                raise Refused("each key is a row's key (text or a number)")
            out.append(_key(k))
        return out

    def _backed(self, agent: str, ag: dict, text: str, what: str) -> None:
        """numbers_backed against everything the agent may read, for the period shown."""
        load = self._load(None)
        vs = self.rt.views(load, self.now)
        reads = ag.get("reads") or []
        known = K.known_numbers(self.spec, [r for r in reads if r.startswith("view:")], [], vs)
        for r in reads:
            if r.startswith("view:") or r in NEVER:
                continue
            rows = self._rows(r, load)
            types = self._types(r)
            known.add(str(len(rows)))
            for row in rows:
                for f, v in row.items():
                    if v is None or f.startswith("_"):
                        continue
                    t = types.get(f, "text")
                    if t in NUMERIC and isinstance(v, str) and D.is_dec(v):
                        known |= K._forms(v, t)
                    elif isinstance(v, str):
                        for m in re.finditer(r"-?[0-9]+(?:\.[0-9]+)?", v):
                            known |= K._forms(m.group(0), "number")
        problems = K.claims(text, known, {})
        if problems:
            raise Refused(f"the {what} has numbers {agent}'s reads don't back: " + "; ".join(problems))


def _match(x, op: str, v, t: str) -> bool:
    """One where condition on a row value (agents.md §3 ops), typed by the field."""
    if op == "is_null":
        return (x is None) == (v is None or v is True)
    if op == "in":
        return any(_match(x, "eq", e, t) for e in v)
    if op == "ne":
        return not _match(x, "eq", v, t)
    if isinstance(x, list):
        return op in ("eq", "contains") and any(_key(e) == _key(v) for e in x)
    if op == "eq":
        if v is None or x is None:
            return x is None and v is None
        if t in NUMERIC:
            a, b = _num(x), _num(v)
            return a is not None and b is not None and D.cmp(a, b) == 0
        if t == "enum":
            return C.norm_key(_key(x)) == C.norm_key(_key(v))
        return _key(x) == _key(v)
    if op == "contains":
        if x is None or v is None:
            return False
        return C.norm_key(_key(v)) in C.norm_key(_key(x))
    if x is None or v is None:
        return False
    if t in NUMERIC:
        a, b = _num(x), _num(v)
        if a is None or b is None:
            return False
        c = D.cmp(a, b)
    elif t == "bool":
        if not isinstance(x, bool) or not isinstance(v, bool):
            return False
        c = _cmp_typed(x, v, t)
    else:
        c = _cmp_typed(_key(x), _key(v), t)
    return {"lt": c < 0, "le": c <= 0, "gt": c > 0, "ge": c >= 0}[op]
