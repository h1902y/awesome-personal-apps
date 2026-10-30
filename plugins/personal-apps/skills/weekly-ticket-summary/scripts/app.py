#!/usr/bin/env python3
"""The app, as an agent plugin runs it (osgen agent format). Stdlib only; run with uv or python3. The agent is the
model: a run stops at each AI step and prints a task file; the agent writes the output and submits it. Every number
comes from the app's views, never from the agent.

    app.py sample                              load the sample (or: app.py run <workflow> --sample)
    app.py run <workflow> [FILE ...]           start a workflow (a load takes files; others run on the period shown)
    app.py submit <run> <step> FILE            hand in an AI step's output (JSON; FILE may be - for stdin)
    app.py show <page|component>               print a page or document as markdown
    app.py mark <key> done|snoozed|open        the buyer's mark on a queue row (snoozed: --for 1d)
    app.py approve <n,…> | reject <n,…>        decide pending approvals (numbers from `app.py show approvals`)
    app.py map <table>.<field>=<column> …      fix a guessed column, then load the file again
    app.py status                              the period, its load, and runs waiting on you

App agents (plans/osgen/agents.md §6): each command checks the agent's allowlist and refuses in plain words.

    app.py query <source> --agent A [--fields a,b] [--where "<field> <op> <value>"]… [--sort f|-f] [--limit N]
    app.py submit <workflow>.<step> FILE --agent A      (or <run> <step> FILE) fill an AI step the agent holds
    app.py run <workflow> --agent A                     start a workflow (trigger agent; every save waits for a yes)
    app.py propose mark <table> --agent A --keys K1,K2 --mark done|snoozed|open [--for 1d]
    app.py propose remap <table> --agent A <field>=<column> …
    app.py open <page> --agent A [--focus C]            print a page (or one component) as markdown
    app.py remember <key> <text|-> --agent A            keep a note
    app.py recall [<key>] --agent A                     the agent's own notes
    app.py handoff <agent> --agent A [--keys K1,K2] [--note TEXT] [--hops N]   the typed handoff note (JSON)

Options: --period P (pick a period; `query --period all` for an agent with app memory), --as-of ISO (default: now,
UTC; the only clock), --data DIR (default: $OSGEN_DATA, else $CLAUDE_PLUGIN_DATA/<slug>, else ~/.h1902y/<slug>).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import core as C  # noqa: E402
from agents import AgentTools, Refused  # noqa: E402
from parse import parse_datetime  # noqa: E402
from render import Renderer  # noqa: E402
from runner import Runtime  # noqa: E402
from store import SqliteStore  # noqa: E402

SPEC_FILE = HERE / "spec.json"


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


def opt(args: list[str], name: str, default=None):
    if name in args:
        i = args.index(name)
        val = args[i + 1] if i + 1 < len(args) else die(f"{name} needs a value")
        del args[i:i + 2]
        return val
    return default


def flag(args: list[str], name: str) -> bool:
    if name in args:
        args.remove(name)
        return True
    return False


def main(argv: list[str]) -> None:
    args = list(argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    spec = json.loads(SPEC_FILE.read_text())
    data = Path(opt(args, "--data") or os.environ.get("OSGEN_DATA") or
                (Path(os.environ["CLAUDE_PLUGIN_DATA"]) / spec["slug"] if os.environ.get("CLAUDE_PLUGIN_DATA") else
                 Path.home() / ".h1902y" / spec["slug"]))
    data.mkdir(parents=True, exist_ok=True)
    as_of = opt(args, "--as-of")
    as_of = parse_datetime(as_of) if as_of else C.iso_datetime(int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000))
    period = opt(args, "--period")
    store = SqliteStore(str(data / "app.db"), spec)
    rt = Runtime(spec, store)
    cmd, rest = args[0], args[1:]
    agent = opt(rest, "--agent")
    try:
        if agent is not None:
            if cmd not in AGENT_COMMANDS:
                die(f"{cmd} isn't an app agent command (have: {', '.join(AGENT_COMMANDS)})")
            tools = AgentTools(spec, rt, store, period=None if period == "all" else period, now=as_of, remap=_remapper(spec, data))
            out = AGENT_COMMANDS[cmd](spec, rt, tools, agent, rest, period, data)
        else:
            out = COMMANDS[cmd](spec, rt, store, rest, as_of, period, data) if cmd in COMMANDS else die(f"no command {cmd}\n{__doc__}")
    except Refused as e:
        die(f"Refused: {e}")
    finally:
        store.commit()
    if out:
        print(out, end="" if out.endswith("\n") else "\n")


def loader(spec: dict) -> str:
    return next(w for w, d in spec["workflows"].items() if any(t["on"] in ("upload", "sample") for t in d["trigger"]))


def report_run(spec, rt, run, data: Path) -> str:
    text = _report_one(spec, rt, run, data)
    for nxt in getattr(run, "after", []):
        text += "\n\nThen " + spec["workflows"][nxt.workflow].get("title", nxt.workflow).lower() + ":\n" + report_run(spec, rt, nxt, data)
    return text


def _report_one(spec, rt, run, data: Path) -> str:
    if run.state == "failed":
        return "Stopped: " + "; ".join(run.problems)
    if run.state == "waiting_approval":
        return "Waiting for approval. Show them with `app.py show approvals`, then `app.py approve N` or `reject N`."
    if run.state == "waiting_ai":
        w = run.waiting
        path = data / "tasks" / f"run{run.row['_row']}-{w['step']}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(w["task"], indent=1, ensure_ascii=False) + "\n")
        lines = [f"AI step `{w['step']}` ({w['task']['task']}) waits for you. Task file: {path}"]
        if w.get("problems"):
            lines += ["Your last output didn't pass these checks; fix them and submit once more (after that the app uses its own fallback):",
                      *[f"- {p}" for p in w["problems"][:12]]]
        submit = (f"app.py submit {run.workflow}.{w['step']} OUTPUT.json --agent <your name>" if str(run.by).startswith("agent:")
                  else f"app.py submit {run.row['_row']} {w['step']} OUTPUT.json")
        lines += [f"Read it, write the output as JSON in the `shape` it gives (a list of rows), keeping to its `rules`, then run: {submit}",
                  "", json.dumps({k: w["task"][k] for k in ("task", "instructions", "rules", "params", "shape")}, ensure_ascii=False)]
        return "\n".join(lines)
    load = run.load or rt.current_load()
    if load and spec["workflows"][run.workflow]["steps"][0]["type"] == "load":
        missing = load.get("columns_missing") or []
        return (f"Loaded the {spec['period'].get('label', '{period}').replace('{period}', load['period'])}: {load.get('rows_read')} rows read, "
                f"{load.get('rows_skipped')} skipped" + (f", columns missing: {', '.join(missing)}" if missing else ", no columns missing") + ".")
    return f"Done: {spec['workflows'][run.workflow].get('title', run.workflow)}."


def cmd_sample(spec, rt, store, rest, as_of, period, data):
    w = loader(spec)
    s = spec["sample"]
    files = {n: (HERE / "sample" / n).read_text(encoding="utf-8") for n in s["files"]}
    run = rt.trigger(w, "sample", as_of=parse_datetime(s["as_of"]), payload={"files": files, "period": s.get("period"), "kind": "sample"})
    return report_run(spec, rt, run, data)


def cmd_run(spec, rt, store, rest, as_of, period, data):
    if not rest:
        die("which workflow? " + ", ".join(spec["workflows"]))
    w, files = rest[0], rest[1:]
    if w not in spec["workflows"]:
        die(f"no workflow {w} (have: {', '.join(spec['workflows'])})")
    if flag(files, "--sample"):
        return cmd_sample(spec, rt, store, [], as_of, period, data)
    ons = {t["on"] for t in spec["workflows"][w]["trigger"]}
    if "upload" in ons:
        if not files:
            die(f"{w} loads data: give the file(s), or use `app.py sample`")
        payload = {"files": {Path(f).name: Path(f).read_text(encoding="utf-8") for f in files}, "period": period,
                   "mapping": json.loads((data / "mapping.json").read_text()) if (data / "mapping.json").exists() else {}}
        run = rt.trigger(w, "upload", as_of=as_of, payload=payload)
    else:
        on = "button" if "button" in ons else next(iter(ons))
        run = rt.trigger(w, on, as_of=as_of, payload={"period": period})
    return report_run(spec, rt, run, data)


def cmd_submit(spec, rt, store, rest, as_of, period, data):
    if len(rest) != 3:
        die("app.py submit <run> <step> FILE")
    run_row, step, f = int(rest[0]), rest[1], rest[2]
    text = sys.stdin.read() if f == "-" else Path(f).read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        die(f"that isn't JSON: {e}")
    if isinstance(value, dict) and isinstance(value.get("rows"), list):
        value = value["rows"]
    store.submit_ai(run_row, step, value)
    run = rt.resume(run_row)
    return report_run(spec, rt, run, data)


def cmd_show(spec, rt, store, rest, as_of, period, data):
    target = rest[0] if rest else spec["skill"].get("output_page") or spec["pages"][0]["id"]
    load = rt.current_load(period)
    if load is None:
        return "No data yet: load a file, or try `app.py sample`."
    r = Renderer(spec, rt.views(load, now=as_of))
    if target == "approvals":
        return r.component({"type": "approvals"})
    known = [p["id"] for p in spec["pages"]] + [c["id"] for p in spec["pages"] for c in p["components"] if c.get("id")]
    if target not in known:
        die(f"no page or component {target} (pages: {', '.join(p['id'] for p in spec['pages'])}, approvals)")
    return r.page(target)


def cmd_mark(spec, rt, store, rest, as_of, period, data):
    if len(rest) < 2:
        die("app.py mark <key> done|snoozed|open [--for 1d]")
    key, mark = rest[0], rest[1]
    span = opt(rest, "--for", "1d")
    queue = next((c for p in spec["pages"] for c in p["components"] if c["type"] == "queue"), None)
    if queue is None:
        die("this app has no queue")
    table = next(op["marks"]["table"] for op in _ops_to_marks(spec, queue["data"][5:]))
    load = rt.current_load(period)
    until = None
    if mark == "snoozed":
        n, unit = int(span[:-1]), span[-1]
        until = C.iso_datetime(C.ms(as_of) + n * (86400000 if unit == "d" else 3600000))
    store.set_mark(table, key, load["period"], None if mark == "open" else mark, until, as_of)
    return f"{key}: {mark}" + (f" until {until[:16].replace('T', ' ')} UTC" if until else "") + "."


def _ops_to_marks(spec, view):
    v = spec["views"][view]
    hits = [op for op in v.get("ops") or [] if "marks" in op]
    if hits:
        return hits
    frm = v["from"]
    return _ops_to_marks(spec, frm[5:]) if isinstance(frm, str) and frm.startswith("view:") else []


def cmd_decide(state):
    def go(spec, rt, store, rest, as_of, period, data):
        pend = [a for a in store.sys("approvals") if a.get("state") == "pending"]
        picks = [int(x) for x in ",".join(rest).split(",") if x.strip()]
        out = []
        for n in picks:
            if not 1 <= n <= len(pend):
                die(f"no pending approval {n}")
            a = pend[n - 1]
            if a.get("run") is None:  # an app agent's proposal (mark, remap): applied by the built-in op on yes
                tools = AgentTools(spec, rt, store, period=period, now=as_of, remap=_remapper(spec, data))
                res = tools.decide(a["_row"], state == "applied")
                out.append(f"{n}: {res['state']} ({res['message']})")
                continue
            store.set_sys("approvals", a, {"state": state, "decided_at": as_of})
            run = rt.resume(a["run"])
            out.append(f"{n}: {state} ({report_run(spec, rt, run, data)})")
        return "\n".join(out)
    return go


def cmd_map(spec, rt, store, rest, as_of, period, data):
    f = data / "mapping.json"
    m = json.loads(f.read_text()) if f.exists() else {}
    for pair in rest:
        left, _, col = pair.partition("=")
        table, _, field = left.partition(".")
        if table not in spec["tables"] or field not in spec["tables"][table]["fields"]:
            die(f"no field {left} (fields: {', '.join(f'{t}.{x}' for t, d in spec['tables'].items() for x in d['fields'])})")
        m.setdefault(table, {})[field] = col
    f.write_text(json.dumps(m, indent=1) + "\n")
    return "Saved. Load the file again to use it."


def cmd_status(spec, rt, store, rest, as_of, period, data):
    load = rt.current_load(period)
    lines = [f"{spec['title']}: " + (f"{spec['period'].get('label', '{period}').replace('{period}', load['period'])}, "
                                     f"{load.get('rows_read')} rows ({load['kind']})" if load else "no data yet")]
    for r in store.sys("runs"):
        if r["state"] in ("waiting_ai", "waiting_approval"):
            lines.append(f"- run {r['_row']} ({r['workflow']}): {r['state'].replace('_', ' ')}")
    return "\n".join(lines)


# ---------- app agents (agents.md §3, §6): the same AgentTools the OS store calls, with --agent <name> ----------

def _remapper(spec, data: Path):
    """A yes to a loader's remap: the buyer's own `map` op (kept for the next load of the file)."""
    def remap(table, mapping):
        f = data / "mapping.json"
        m = json.loads(f.read_text()) if f.exists() else {}
        m.setdefault(table, {}).update(mapping)
        f.write_text(json.dumps(m, indent=1) + "\n")
        return "Saved: load the file again to use it."
    return remap


def opts_all(args: list[str], name: str) -> list[str]:
    out = []
    while name in args:
        out.append(opt(args, name))
    return out


def _value(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _where(text: str):
    t = text.strip()
    if t.startswith(("[", "{")):
        w = json.loads(t)
        return w if isinstance(w, list) else [w]
    parts = t.split(None, 2)
    if len(parts) < 2:
        die(f"--where wants \"<field> <op> <value>\", not {text!r}")
    field, op = parts[0], parts[1]
    if len(parts) == 2:
        return [{"field": field, "op": op}]
    raw = parts[2]
    value = _value(raw)
    if op == "in" and not isinstance(value, list):
        value = [_value(x.strip()) for x in raw.split(",")]
    return [{"field": field, "op": op, "value": value}]


def _dump(x) -> str:
    return json.dumps(x, indent=1, ensure_ascii=False)


def ag_query(spec, rt, tools, agent, rest, period, data):
    fields = opt(rest, "--fields")
    where = [w for t in opts_all(rest, "--where") for w in _where(t)]
    sort = [x for t in opts_all(rest, "--sort") for x in t.split(",") if x]
    limit = opt(rest, "--limit")
    if not rest:
        die("app.py query <source> --agent A [--fields a,b] [--where \"<field> <op> <value>\"] [--sort f|-f] [--limit N]")
    q = {"where": where, "sort": sort}
    if fields:
        q["fields"] = [f for f in fields.split(",") if f]
    if limit is not None:
        q["limit"] = int(limit) if limit.lstrip("-").isdigit() else limit
    if period is not None:
        q["period"] = period
    return _dump(tools.query(agent, rest[0], q))


def ag_submit(spec, rt, tools, agent, rest, period, data):
    if len(rest) == 3:
        run_row, step, f = int(rest[0]), rest[1], rest[2]
        row = next((r for r in rt.store.sys("runs") if r["_row"] == run_row), None)
        target, run = f"{row['workflow'] if row else '?'}.{step}", run_row
    elif len(rest) == 2:
        target, f, run = rest[0], rest[1], None
    else:
        die("app.py submit <workflow>.<step> FILE --agent A   (or <run> <step> FILE)")
    text = sys.stdin.read() if f == "-" else Path(f).read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        die(f"that isn't JSON: {e}")
    if isinstance(value, dict) and isinstance(value.get("rows"), list):
        value = value["rows"]
    return report_run(spec, rt, tools.fill(agent, target, value, run), data)


def ag_run(spec, rt, tools, agent, rest, period, data):
    if len(rest) != 1:
        die("app.py run <workflow> --agent A")
    return report_run(spec, rt, tools.run(agent, rest[0]), data)


def ag_propose(spec, rt, tools, agent, rest, period, data):
    if len(rest) < 2:
        die("app.py propose mark|remap <table> --agent A …")
    kind, table, more = rest[0], rest[1], rest[2:]
    if more and more[0].strip().startswith("{"):
        payload = json.loads(more[0])
    elif kind == "mark":
        keys, mark, span = opt(more, "--keys"), opt(more, "--mark"), opt(more, "--for")
        payload = {"keys": [k for k in (keys or "").split(",") if k], "mark": mark}
        if span is not None:
            payload["for"] = span
    else:
        payload = {"mapping": dict(x.partition("=")[::2] for x in more)}
    res = tools.propose(agent, kind, table, payload)
    return f"Proposed: {res['summary']}. It waits for the user: `app.py show approvals`, then `app.py approve N` or `reject N`."


def ag_open(spec, rt, tools, agent, rest, period, data):
    focus = opt(rest, "--focus")
    if len(rest) != 1:
        die("app.py open <page> --agent A [--focus C]")
    res = tools.open(agent, rest[0], focus)
    return res["markdown"] if res["markdown"] is not None else "No data yet: load a file, or try `app.py sample`."


def ag_recall(spec, rt, tools, agent, rest, period, data):
    notes = tools.recall(agent, rest[0] if rest else None)
    return "\n".join(f"- {n['key']}: {n['text']}" for n in notes) if notes else "No notes yet."


def ag_remember(spec, rt, tools, agent, rest, period, data):
    if len(rest) != 2:
        die("app.py remember <key> <text|-> --agent A   (app.py recall --agent A reads your notes)")
    text = sys.stdin.read() if rest[1] == "-" else rest[1]
    n = tools.remember(agent, rest[0], text)
    return f"Kept {n['key']}" + (f" for {n['period']}." if n["period"] else ".")


def ag_handoff(spec, rt, tools, agent, rest, period, data):
    keys, note, hops = opt(rest, "--keys"), opt(rest, "--note"), opt(rest, "--hops")
    if len(rest) != 1:
        die("app.py handoff <agent> --agent A [--keys K1,K2] [--note TEXT] [--hops N]")
    n = {"keys": [k for k in (keys or "").split(",") if k], "note": note or ""}
    if hops is not None:
        n["hops"] = int(hops) if hops.isdigit() else hops
    if period is not None:
        n["period"] = period
    return _dump(tools.handoff(agent, rest[0], n))


AGENT_COMMANDS = {"query": ag_query, "submit": ag_submit, "run": ag_run, "propose": ag_propose, "open": ag_open,
                  "remember": ag_remember, "recall": ag_recall, "handoff": ag_handoff}

COMMANDS = {"sample": cmd_sample, "run": cmd_run, "submit": cmd_submit, "show": cmd_show, "mark": cmd_mark,
            "approve": cmd_decide("applied"), "reject": cmd_decide("rejected"), "map": cmd_map, "status": cmd_status}

if __name__ == "__main__":
    main(sys.argv[1:])
