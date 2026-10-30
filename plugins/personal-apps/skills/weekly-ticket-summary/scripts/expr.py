"""osgen expression evaluator in Python: the same as runtime/js/expr.ts."""
from __future__ import annotations

import core as C
import dec as D

NUMERIC = {"int", "number", "hours", "money"}


def _compare(a, b, ta, tb) -> int:
    if ta in NUMERIC or tb in NUMERIC:
        return D.cmp(a, b)
    if ta == "bool" or tb == "bool":
        return 0 if a == b else 1 if a else -1
    if ta in ("date", "datetime") and tb in ("date", "datetime") and ta != tb:
        x, y = C.ms(a), C.ms(b)
        return (x > y) - (x < y)
    return C.cmp_text(str(a), str(b))


_CMP = {"=": lambda c: c == 0, "!=": lambda c: c != 0, "<": lambda c: c < 0, "<=": lambda c: c <= 0,
        ">": lambda c: c > 0, ">=": lambda c: c >= 0}


def _s(v) -> str:
    return ("true" if v else "false") if isinstance(v, bool) else str(v)


def evaluate(n: dict, scope):
    k = n["k"]
    if k == "lit":
        return n["v"]
    if k == "name":
        return scope(n["path"])
    if k == "list":
        return [evaluate(x, scope) for x in n["items"]]
    if k == "neg":
        a = evaluate(n["a"], scope)
        return None if a is None else D.neg(a)
    if k == "not":
        a = evaluate(n["a"], scope)
        return None if a is None else not a
    if k == "and":
        unknown = False
        for x in n["args"]:
            v = evaluate(x, scope)
            if v is False:
                return False
            if v is None:
                unknown = True
        return None if unknown else True
    if k == "or":
        unknown = False
        for x in n["args"]:
            v = evaluate(x, scope)
            if v is True:
                return True
            if v is None:
                unknown = True
        return None if unknown else False
    if k == "bin":
        a, b = evaluate(n["a"], scope), evaluate(n["b"], scope)
        if a is None or b is None:
            return None
        op = n["op"]
        return D.add(a, b) if op == "+" else D.sub(a, b) if op == "-" else D.mul(a, b) if op == "*" else D.div(a, b)
    if k == "cmp":
        a, b = evaluate(n["a"], scope), evaluate(n["b"], scope)
        if a is None or b is None:
            return None
        return _CMP[n["op"]](_compare(a, b, n["a"]["t"], n["b"]["t"]))
    if k == "in":
        a = evaluate(n["a"], scope)
        if a is None:
            return None
        unknown = False
        for x in n["list"]:
            v = evaluate(x, scope)
            if v is None:
                unknown = True
                continue
            if _compare(a, v, n["a"]["t"], x["t"]) == 0:
                return True
        return None if unknown else False
    if k == "isnull":
        is_null = evaluate(n["a"], scope) is None
        return not is_null if n["neg"] else is_null
    if k == "call":
        return _call(n, scope)
    raise ValueError(f"expr: unknown node {k}")


def _call(n: dict, scope):
    f = n["f"]
    if f == "coalesce":
        for x in n["args"]:
            v = evaluate(x, scope)
            if v is not None:
                return v
        return None
    if f == "if":
        return evaluate(n["args"][1], scope) if evaluate(n["args"][0], scope) is True else evaluate(n["args"][2], scope)
    args = [evaluate(x, scope) for x in n["args"]]
    if f == "join_nonempty":
        sep, *rest = args
        return ("" if sep is None else _s(sep)).join(_s(x) for x in rest if x is not None and x != "")
    if f == "bucket":
        return C.bucket(args[0], args[1] or [], args[2] or [])
    if f == "fmt_money":
        return C.fmt_money(args[0], args[1])
    if any(a is None for a in args):
        return None
    a = args[0]
    b = args[1] if len(args) > 1 else None
    if f == "round_half_up":
        return D.round_(a, int(b))
    if f == "abs":
        return D.abs_(a)
    if f == "min":
        return a if _compare(a, b, n["args"][0]["t"], n["args"][1]["t"]) <= 0 else b
    if f == "max":
        return a if _compare(a, b, n["args"][0]["t"], n["args"][1]["t"]) >= 0 else b
    if f == "norm_key":
        return C.norm_key(_s(a))
    if f == "lower":
        return _s(a).lower()
    if f == "collapse_ws":
        return C.collapse_ws(_s(a))
    if f == "contains":
        return _s(b) in _s(a)
    if f == "concat":
        return "".join(_s(x) for x in args)
    if f == "hours_between":
        return C.hours_between(a, b)
    if f == "days_between":
        return C.days_between(a, b)
    if f == "week_of":
        return C.week_of(a)
    if f == "fmt_num":
        return C.fmt_num(a)
    if f == "fmt_pct":
        return C.fmt_pct(a)
    if f == "fmt_hours":
        return C.fmt_hours(a)
    if f == "fmt_date":
        return C.fmt_date(a)
    if f == "first_sentence":
        return C.first_sentence(_s(a))
    raise ValueError(f"expr: no function {f}")
