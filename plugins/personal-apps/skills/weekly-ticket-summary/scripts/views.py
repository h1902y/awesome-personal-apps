"""osgen views in Python: the same code as runtime/js/views.ts (see there)."""
from __future__ import annotations

import json
import re
from functools import cmp_to_key

import core as C
import dec as D
from expr import evaluate
from template import render

NUMERIC = {"int", "number", "hours", "money"}


def _cmp_typed(x, y, t) -> int:
    if t in NUMERIC:
        return D.cmp(x, y)
    if t == "bool":
        return 0 if x == y else 1 if x else -1
    return C.cmp_text(str(x), str(y))


def _key(v) -> str:
    """String(v) as JS writes it, for join and group keys."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _sort(rows, keys, types):
    idx = list(enumerate(rows))

    def cmp(a, b):
        ia, ra = a
        ib, rb = b
        for k in keys:
            x, y = ra.get(k["by"]), rb.get(k["by"])
            if x is None or y is None:
                if x is None and y is None:
                    continue
                nulls_first = k.get("nulls") == "first"
                return -1 if (x is None) == nulls_first else 1
            c = _cmp_typed(x, y, types(k["by"]))
            if c:
                return -c if k.get("dir") == "desc" else c
        xa, xb = ra.get("_row", ia), rb.get("_row", ib)
        if xa != xb:
            return -1 if xa < xb else 1
        return ia - ib

    return [r for _, r in sorted(idx, key=cmp_to_key(cmp))]


def periods(store) -> list[dict]:
    latest: dict[str, dict] = {}
    for l in store.sys("loads"):
        cur = latest.get(l["period"])
        if cur is None or l["_row"] > cur["_row"]:
            latest[l["period"]] = l
    return sorted(latest.values(), key=cmp_to_key(lambda a, b: C.cmp_text(b["period"], a["period"])))


class Views:
    def __init__(self, spec: dict, store, ctx: dict):
        self.spec, self.store, self.ctx = spec, store, ctx
        self._memo: dict[str, list] = {}

    def view(self, name: str) -> list[dict]:
        load = self.ctx.get("load")
        key = f"{name}@{load['_row'] if load else '-'}"
        if key in self._memo:
            return self._memo[key]
        v = self.spec["views"].get(name)
        if v is None:
            raise KeyError(f"no view {name}")
        ops = v.get("ops") or []
        if v.get("across") == "periods":
            rows = []
            loads = list(reversed(periods(self.store)))
            at = next((i for i, o in enumerate(ops) if "series" in o), -1)
            before = ops if at < 0 else ops[:at]
            for i, l in enumerate(loads):
                sub = Views(self.spec, self.store, {"load": l, "prev": loads[i - 1] if i else None, "now": self.ctx.get("now")})
                for r in sub._run(sub._source(v["from"]), before, name):
                    rows.append({**r, "period": l["period"]})
            rows = self._run(rows, [] if at < 0 else ops[at:], name, [l["period"] for l in loads])
        else:
            rows = self._run(self._source(v["from"]), ops, name)
        self._memo[key] = rows
        return rows

    def record(self, name: str):
        rows = self.view(name)
        return rows[0] if rows else None

    def _source(self, frm):
        if isinstance(frm, dict) and "first" in frm:
            for f in frm["first"]:
                rows = self._source(f)
                if rows:
                    return rows
            return []
        ref = str(frm)
        if ref.startswith("view:"):
            return self.view(ref[5:])
        if ref.startswith("sys."):
            return self.store.sys(ref[4:])
        if ref.startswith("ai:"):
            wf, step = ref[3:].split(".")
            return self.store.ai(wf, step, self.ctx.get("load")) or []
        scope = (self.spec["tables"].get(ref) or {}).get("scope")
        return self.store.rows(ref, None if scope == "app" else self.ctx.get("load"))

    def _scope(self, row):
        load, prev = self.ctx.get("load"), self.ctx.get("prev")

        def get(path):
            key = ".".join(path)
            if key in row:
                return row[key]
            if path[0] == "load" and load:
                return load.get(path[1])
            if path[0] == "prev" and prev:
                return prev.get(path[1])
            return None
        return get

    def _types(self, name):
        out = self.spec["views"].get(name, {}).get("out") or {}
        return lambda f: out.get(f, "text")

    def _run(self, rows, ops, name, all_periods=None):
        for i, op in enumerate(ops):
            kind = next(iter(op))
            p = op[kind]
            if kind == "filter":
                rows = [r for r in rows if evaluate(p, self._scope(r)) is True]
            elif kind == "derive":
                new = []
                for r in rows:
                    out = dict(r)
                    for f, tree in p.items():
                        out[f] = evaluate(tree, self._scope(out))
                    new.append(out)
                rows = new
            elif kind == "select":
                new = []
                for r in rows:
                    out = {"_row": r["_row"]} if "_row" in r else {}
                    if isinstance(p, list):
                        for f in p:
                            out[f] = r.get(f)
                    else:
                        for to, frm in p.items():
                            out[to] = r.get(frm)
                    new.append(out)
                rows = new
            elif kind == "join":
                rows = self._join(rows, p)
            elif kind == "group":
                rows = _group(rows, p)
            elif kind == "sort":
                rows = _sort(rows, p, self._types(name))
            elif kind == "limit":
                rows = rows[:p]
            elif kind == "number":
                rows = [{**r, p["as"]: str(i + 1)} for i, r in enumerate(rows)]
            elif kind == "marks":
                rows = self._marks(rows, p["table"])
            elif kind == "compare":
                rows = self._compare(rows, p, ops[:i], name)
            elif kind == "series":
                rows = _series(rows, p, all_periods or [])
            elif kind == "text":
                recs = {v: self.record(v) for v in op.get("views") or []}
                new = []
                for r in rows:
                    out = dict(r)
                    for f, tpl in p.items():
                        out[f] = render(tpl, out, recs)
                    new.append(out)
                rows = new
            elif kind == "first":
                rows = rows[:1]
            else:
                raise ValueError(f"view {name}: unknown op {kind}")
        return rows

    def _join(self, rows, p):
        tables = self.spec["tables"]
        if p.get("ref"):
            target = self._ref_target(p["ref"])
            scope = tables[target].get("scope")
            by_row = {r["_row"]: r for r in self.store.rows(target, None if scope == "app" else self.ctx.get("load"))}
            out_rows = []
            for r in rows:
                t = by_row.get(r.get(p["ref"]))
                out = dict(r)
                for f in tables[target]["fields"]:
                    out[f"{p['ref']}.{f}"] = t.get(f) if t else None
                out_rows.append(out)
            return out_rows
        other = self.view(p["view"]) if p.get("view") else self._source(f"ai:{p['ai']}")
        if not p.get("on"):
            hit = other[0] if other else None
            return [{**r, **{as_: (hit.get(f) if hit else None) for as_, f in (p.get("fields") or {}).items()}} for r in rows]
        to = p.get("to") or "key"
        idx: dict[str, dict] = {}
        for o in other:
            k = o.get(to)
            if k is not None and _key(k) not in idx:
                idx[_key(k)] = o
        out_rows = []
        for r in rows:
            on = r.get(p["on"])
            hit = idx.get(_key(on)) if on is not None else None
            out = dict(r)
            for as_, f in (p.get("fields") or {}).items():
                out[as_] = hit.get(f) if hit else None
            out_rows.append(out)
        return out_rows

    def _ref_target(self, field):
        for t in self.spec["tables"].values():
            f = t["fields"].get(field)
            if f and f.get("type") == "ref":
                return f["to"]
        raise KeyError(f"no ref field {field}")

    def _marks(self, rows, table):
        key = (self.spec["tables"].get(table) or {}).get("key", "key")
        load = self.ctx.get("load")
        period = load["period"] if load else ""
        now = self.ctx.get("now") or (load["as_of"] if load else "")
        marks = {}
        for m in self.store.sys("marks"):
            if m.get("table") == table and m.get("period") == period:
                marks[_key(m.get("key"))] = m
        out = []
        for r in rows:
            m = marks.get(_key(r.get(key)))
            live = m is not None and not (m.get("mark") == "snoozed" and m.get("until") is not None and C.cmp_text(str(m["until"]), now) <= 0)
            out.append({**r, "mark": m["mark"] if live else None, "mark_until": m.get("until") if live else None})
        return out

    def _compare(self, rows, p, before, name):
        ps = periods(self.store)
        load = self.ctx.get("load")
        at = next((i for i, l in enumerate(ps) if load and l["_row"] == load["_row"]), -1)
        prev_load = ps[at + 1] if at >= 0 and at + 1 < len(ps) else None
        prev_rows = []
        if prev_load:
            sub = Views(self.spec, self.store, {"load": prev_load, "prev": ps[at + 2] if at + 2 < len(ps) else None, "now": self.ctx.get("now")})
            prev_rows = sub._run(sub._source(self.spec["views"][name]["from"]), before, name)
        prev = {_key(r.get(p["by"])): (r.get(p["value"]) if r.get(p["value"]) is not None else "0") for r in prev_rows}
        seen, out = set(), []
        for r in rows:
            k = _key(r.get(p["by"]))
            seen.add(k)
            now = r.get(p["value"]) if r.get(p["value"]) is not None else "0"
            b = prev.get(k, "0")
            out.append({"by": r.get(p["by"]), "before": b if prev_load else None, "now": now,
                        "change": D.sub(now, b) if prev_load else None})
        for r in prev_rows:
            if _key(r.get(p["by"])) not in seen:
                b = r.get(p["value"]) if r.get(p["value"]) is not None else "0"
                out.append({"by": r.get(p["by"]), "before": b, "now": "0", "change": D.neg(b)})
        return out


def _group(rows, p):
    groups: dict[str, list] = {}
    for r in rows:
        k = json.dumps([r.get(f) for f in p["by"]], ensure_ascii=False, separators=(",", ":"))
        groups.setdefault(k, []).append(r)
    if not p["by"] and not groups:
        groups["[]"] = []
    n = len(rows)
    out = []
    for g in groups.values():
        o = {f: (g[0].get(f) if g else None) for f in p["by"]}
        for as_, fn in p["agg"].items():
            m = re.fullmatch(r"(\w+)\(([\w.]*)\)", fn)
            vals = [r.get(m.group(2)) for r in g if r.get(m.group(2)) is not None] if m.group(2) else []
            f = m.group(1)
            if f == "count":
                o[as_] = str(len(g))
            elif f == "share":
                o[as_] = D.div(D.mul(str(len(g)), "100"), str(n))
            elif f == "sum":
                acc = "0"
                for v in vals:
                    acc = D.add(acc, v)
                o[as_] = acc
            elif f in ("min", "max"):
                if not vals:
                    o[as_] = None
                else:
                    # numbers as decimals; dates, datetimes and text by code point (A3: max(sent_on))
                    cmp = D.cmp if all(D.is_dec(v) for v in vals) else (lambda a, b: C.cmp_text(_key(a), _key(b)))
                    acc = vals[0]
                    for v in vals[1:]:
                        if (cmp(acc, v) > 0) if f == "min" else (cmp(acc, v) < 0):
                            acc = v
                    o[as_] = acc
            elif f == "first":
                o[as_] = g[0].get(m.group(2)) if g else None
            elif f == "avg":
                if not vals:
                    o[as_] = None
                else:
                    acc = "0"
                    for v in vals:
                        acc = D.add(acc, v)
                    o[as_] = D.div(acc, str(len(vals)))
        out.append(o)
    return out


def _series(rows, p, all_periods):
    ps = all_periods[-p["periods"]:] if p.get("periods") else all_periods
    bys, val = [], {}
    for r in rows:
        if str(r.get("period")) not in ps:
            continue
        b = r.get(p["by"])
        if not any(x == b and type(x) is type(b) for x in bys):
            bys.append(b)
        val[json.dumps([r.get("period"), b])] = r.get(p["value"]) if r.get(p["value"]) is not None else "0"
    return [{"period": period, "by": b, "value": val.get(json.dumps([period, b]), "0")} for b in bys for period in ps]


class MemoryStore:
    def __init__(self):
        self.tables: dict[str, list] = {}
        self.system = {k: [] for k in ("loads", "runs", "ai_outputs", "approvals", "marks", "schedules", "connections", "agent_memory")}
        self.outputs: dict[str, list] = {}

    def rows(self, table, load):
        all_ = self.tables.get(table, [])
        return [r for r in all_ if r.get("_load") == load["_row"]] if load else all_

    def sys(self, table):
        return self.system.get(table, [])

    def ai(self, workflow, step, load):
        return self.outputs.get(f"{workflow}.{step}@{load['_row'] if load else '-'}")
