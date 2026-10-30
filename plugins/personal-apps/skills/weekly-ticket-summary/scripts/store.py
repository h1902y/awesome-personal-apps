"""osgen stores in Python (plans/osgen/tables.md §3, §5): a memory store (golden runs, tests) and a sqlite3 store
(the agent plugins: one file per app). Both give the read side views.py needs and the write side runner.py uses.
Rows carry `_row` (and `_load` for load tables); values are the runtime's canonical values (decimal strings, ISO
dates), stored as JSON so the two stores hold exactly the same thing.
"""
from __future__ import annotations

import json
import sqlite3

SYS = ("loads", "runs", "ai_outputs", "approvals", "marks", "schedules", "connections", "agent_memory")


class MemoryStore:
    def __init__(self):
        self.tables: dict[str, list] = {}
        self.system: dict[str, list] = {k: [] for k in SYS}
        self._next = 1
        self._pending: dict[tuple, object] = {}
        self._tries: dict[tuple, int] = {}
        self._problems: dict[tuple, list] = {}

    def _id(self) -> int:
        n = self._next
        self._next += 1
        return n

    # read side (views.py)
    def rows(self, table, load):
        all_ = self.tables.get(table, [])
        return [r for r in all_ if r.get("_load") == load["_row"]] if load else list(all_)

    def sys(self, table):
        return self.system.get(table, [])

    def ai(self, workflow, step, load):
        hits = [o for o in self.system["ai_outputs"] if o["workflow"] == workflow and o["step"] == step
                and o["load"] == (load["_row"] if load else None)]
        return hits[-1]["value"] if hits else None

    # write side (runner.py)
    def add_sys(self, table, row):
        r = {"_row": self._id(), **row}
        self.system[table].append(r)
        return r

    def set_sys(self, table, row, changes):
        target = row if isinstance(row, dict) else next(r for r in self.system[table] if r["_row"] == row)
        for r in self.system[table]:
            if r["_row"] == target["_row"]:
                r.update(changes)
                target.update(changes)
                return r

    def save_rows(self, table, rows, load, mode, key):
        cur = self.tables.setdefault(table, [])
        lid = load["_row"] if load else None
        if mode == "replace_load":
            cur[:] = [r for r in cur if r.get("_load") != lid]
        if mode == "upsert":
            by_key = {r.get(key): r for r in cur if r.get("_load") == lid}
            for row in rows:
                if row.get(key) in by_key:
                    by_key[row.get(key)].update(row)
                else:
                    cur.append({"_row": self._id(), **({"_load": lid} if load else {}), **row})
            return
        for row in rows:
            cur.append({"_row": self._id(), **({"_load": lid} if load else {}), **row})

    def add_ai_output(self, run, ai_step, rows, filled_by, check, problems):
        # one checked output per (run, step): a resumed run replaces what it wrote before
        self.system["ai_outputs"] = [o for o in self.system["ai_outputs"] if not (o["run"] == run.row["_row"] and o["step"] == ai_step["id"])]
        self.add_sys("ai_outputs", {"run": run.row["_row"], "workflow": run.workflow, "step": ai_step["id"],
                                    "task": ai_step["task"], "load": run.load["_row"] if run.load else None,
                                    "value": rows, "filled_by": filled_by, "check": check, "problems": problems,
                                    "at": run.as_of})

    def pending_ai(self, run_row, step):
        return self._pending.get((run_row, step))

    def submit_ai(self, run_row, step, value):
        self._pending[(run_row, step)] = value

    def clear_pending_ai(self, run_row, step):
        self._pending.pop((run_row, step), None)

    def tries(self, run_row, step):
        return self._tries.get((run_row, step), 0)

    def set_tries(self, run_row, step, n):
        self._tries[(run_row, step)] = n

    def problems(self, run_row, step):
        """The check problems an AI step's last output had, kept until the step passes (a resumed run shows them)."""
        return list(self._problems.get((run_row, step), []))

    def set_problems(self, run_row, step, problems):
        if problems:
            self._problems[(run_row, step)] = list(problems)
        else:
            self._problems.pop((run_row, step), None)

    def approval_for(self, run_row, step):
        hits = [a for a in self.system["approvals"] if a["run"] == run_row and a["step"] == step]
        return hits[-1]["state"] if hits else None

    def set_mark(self, table, key, period, mark, until, at):
        self.system["marks"] = [m for m in self.system["marks"] if not (m["table"] == table and m["key"] == key and m["period"] == period)]
        if mark:
            self.add_sys("marks", {"table": table, "key": key, "period": period, "mark": mark, "until": until, "at": at})

    def delete_load(self, load_row):
        for t in self.tables.values():
            t[:] = [r for r in t if r.get("_load") != load_row]
        for name in ("ai_outputs",):
            self.system[name] = [o for o in self.system[name] if o.get("load") != load_row]
        self.system["loads"] = [l for l in self.system["loads"] if l["_row"] != load_row]


class SqliteStore(MemoryStore):
    """The agent plugins' store: the same data model as MemoryStore, kept in one sqlite3 file. Each table is a
    `t_<table>` / `sys_<table>` with `_row`, `_load` and the row as JSON; everything is loaded on open and written
    back on `commit()` (an app's data is one person's weeks: small)."""

    def __init__(self, path: str, spec: dict):
        super().__init__()
        self.path, self.spec = path, spec
        self.db = sqlite3.connect(path)
        self.db.execute("create table if not exists meta (k text primary key, v text)")
        names = [f"t_{t}" for t in spec["tables"]] + [f"sys_{t}" for t in SYS] + ["state"]
        for n in names:
            self.db.execute(f"create table if not exists {n} (_row integer primary key, _load integer, data text)")
        for t in spec["tables"]:
            self.tables[t] = [{"_row": r, **({"_load": l} if l is not None else {}), **json.loads(d)}
                              for r, l, d in self.db.execute(f"select _row, _load, data from t_{t} order by _row")]
        for t in SYS:
            self.system[t] = [{"_row": r, **json.loads(d)} for r, _, d in self.db.execute(f"select _row, _load, data from sys_{t} order by _row")]
        state = dict(self.db.execute("select k, v from meta").fetchall())
        self._next = int(state.get("next", "1"))
        self._pending = {tuple(json.loads(k)): v for k, v in json.loads(state.get("pending", "[]"))}
        self._tries = {tuple(json.loads(k)): v for k, v in json.loads(state.get("tries", "[]"))}
        self._problems = {tuple(json.loads(k)): v for k, v in json.loads(state.get("problems", "[]"))}

    def commit(self):
        with self.db:
            for t, rows in self.tables.items():
                self.db.execute(f"delete from t_{t}")
                self.db.executemany(f"insert into t_{t} values (?, ?, ?)", [
                    (r["_row"], r.get("_load"), json.dumps({k: v for k, v in r.items() if k not in ("_row", "_load")}, ensure_ascii=False))
                    for r in rows])
            for t, rows in self.system.items():
                self.db.execute(f"delete from sys_{t}")
                self.db.executemany(f"insert into sys_{t} values (?, null, ?)", [
                    (r["_row"], json.dumps({k: v for k, v in r.items() if k != "_row"}, ensure_ascii=False)) for r in rows])
            self.db.executemany("insert or replace into meta values (?, ?)", [
                ("next", str(self._next)),
                ("pending", json.dumps([[json.dumps(list(k)), v] for k, v in self._pending.items()])),
                ("tries", json.dumps([[json.dumps(list(k)), v] for k, v in self._tries.items()])),
                ("problems", json.dumps([[json.dumps(list(k)), v] for k, v in self._problems.items()], ensure_ascii=False))])
