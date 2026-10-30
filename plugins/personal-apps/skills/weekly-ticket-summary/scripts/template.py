"""osgen engine, templates, in Python: the same language as runtime/js/template.ts (see there for the syntax)."""
from __future__ import annotations

import json
import math
import re

import core as C
import dec as D
from table import js_str, trim

def to_str(x) -> str:
    if x is None:
        return ""
    if isinstance(x, list):
        return ", ".join(to_str(v) for v in x)
    if isinstance(x, dict):
        return json.dumps(canon(x), ensure_ascii=False, separators=(",", ":"))
    if isinstance(x, (int, float, bool)):
        return js_str(x)
    return str(x)


def canon(x):
    """Values as JavaScript holds them: integral floats become ints (so 12.0 prints and serialises as 12)."""
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, float):
        return int(x) if x == int(x) and abs(x) < 2 ** 53 else x
    if isinstance(x, int):
        return x
    if isinstance(x, dict):
        return {k: canon(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [canon(v) for v in x]
    return x


def _cell(s: str) -> str:
    return " ".join(p for p in re.split(r"\s+", s) if p).replace("|", "\\|")


def _dec(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return D.from_(x)
    return x if D.is_dec(x) else None


def _first_word(x) -> str:
    return re.split(r"\s+", trim(to_str(x)))[0]


def _count(x) -> str:
    return str(len(x)) if isinstance(x, (list, dict)) else "0"


FILTERS = {
    "num": lambda x: x if _dec(x) is None else C.fmt_num(_dec(x)),
    "pct": lambda x: x if _dec(x) is None else C.fmt_pct(_dec(x)),
    "hours": lambda x: x if _dec(x) is None else C.fmt_hours(_dec(x)),
    "money": lambda x: x if _dec(x) is None else C.fmt_money(_dec(x), None),
    "date": lambda x: x[:10] if isinstance(x, str) and re.match(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", x) else x,
    "round": lambda x: x if _dec(x) is None else D.round_(_dec(x), 0),
    "lower": lambda x: to_str(x).lower(),
    "upper": lambda x: to_str(x).upper(),
    "first_word": _first_word,
    "first_sentence": lambda x: C.first_sentence(to_str(x)),
    "trim": lambda x: trim(to_str(x)),
    "cell": lambda x: _cell(to_str(x)),
    "count": _count,
}


def lookup(scopes: list[dict], path: str):
    if path == ".":
        return scopes[0].get(".", scopes[0]) if scopes else None
    head, *rest = path.split(".")
    v = None
    for s in scopes:
        if isinstance(s, dict) and head in s:
            v = s[head]
            break
    for k in rest:
        if v is None:
            return None
        if isinstance(v, list) and re.fullmatch(r"[0-9]+", k):
            v = v[int(k)] if int(k) < len(v) else None
        elif isinstance(v, dict):
            v = v.get(k)
        else:
            v = None
    return v


def truthy(v) -> bool:
    if isinstance(v, (list, dict)):
        return len(v) > 0
    if isinstance(v, float) and v != v:
        return False
    return bool(v)


_EACH = re.compile(r'#each\s+(\S+)((?:\s+\w+=(?:"[^"]*"|\S+))*)')
_OPT = re.compile(r'(\w+)=(?:"([^"]*)"|(\S+))')


def parse(src: str) -> list:
    root: list = []
    stack: list[dict] = [{"node": None, "into": root}]
    i, text = 0, []

    def flush():
        if text:
            stack[-1]["into"].append("".join(text))
            text.clear()

    while i < len(src):
        c = src[i]
        if c == "{" and src[i + 1:i + 2] == "{":
            text.append("{"); i += 2; continue
        if c == "}" and src[i + 1:i + 2] == "}":
            text.append("}"); i += 2; continue
        if c != "{":
            text.append(c); i += 1; continue
        end = src.find("}", i)
        if end < 0:
            raise ValueError(f"template: unclosed {{ at {i}")
        tag = trim(src[i + 1:end])
        i = end + 1
        flush()
        top = stack[-1]
        if m := _EACH.fullmatch(tag):
            opts = {x.group(1): x.group(2) if x.group(2) is not None else x.group(3) for x in _OPT.finditer(m.group(2))}
            node = {"k": "each", "path": m.group(1), "limit": int(opts["limit"]) if opts.get("limit") else None,
                    "sep": opts.get("sep", ""), "body": []}
            top["into"].append(node)
            stack.append({"node": node, "into": node["body"]})
        elif m := re.fullmatch(r"#if\s+(\S+)", tag):
            node = {"k": "if", "path": m.group(1), "then": [], "else": []}
            top["into"].append(node)
            stack.append({"node": node, "into": node["then"]})
        elif tag == "#else":
            if not top["node"] or top["node"]["k"] != "if":
                raise ValueError("template: {#else} outside {#if}")
            top["into"] = top["node"]["else"]
        elif m := re.fullmatch(r"#plural\s+(\S+)", tag):
            node = {"k": "plural", "path": m.group(1), "one": [], "many": []}
            top["into"].append(node)
            stack.append({"node": node, "into": node["one"]})
        elif m := re.fullmatch(r"/(each|if|plural)", tag):
            if not top["node"] or top["node"]["k"] != m.group(1):
                raise ValueError(f"template: {{{tag}}} doesn't close an open block")
            stack.pop()
        elif re.fullmatch(r"[@\w.]+(\|\w+)*", tag, re.ASCII):
            path, *filters = tag.split("|")
            for f in filters:
                if f not in FILTERS:
                    raise ValueError(f"template: unknown filter {f}")
            top["into"].append({"k": "v", "path": path, "filters": filters})
        else:
            raise ValueError(f"template: can't read {{{tag}}}")
    flush()
    if len(stack) > 1:
        raise ValueError("template: a block isn't closed")
    return _split_plurals(root)


def _split_plurals(nodes: list) -> list:
    out = []
    for n in nodes:
        if isinstance(n, str):
            out.append(n)
        elif n["k"] == "plural":
            one, many = [], []
            into = one
            for x in _split_plurals(n["one"]):
                if isinstance(x, str) and into is one and "|" in x:
                    at = x.index("|")
                    if at:
                        one.append(x[:at])
                    into = many
                    if x[at + 1:]:
                        many.append(x[at + 1:])
                else:
                    into.append(x)
            out.append({**n, "one": one, "many": many})
        elif n["k"] == "each":
            out.append({**n, "body": _split_plurals(n["body"])})
        elif n["k"] == "if":
            out.append({**n, "then": _split_plurals(n["then"]), "else": _split_plurals(n["else"])})
        else:
            out.append(n)
    return out


def _run(nodes: list, scopes: list[dict]) -> str:
    out = []
    for n in nodes:
        if isinstance(n, str):
            out.append(n)
            continue
        k = n["k"]
        if k == "v":
            v = lookup(scopes, n["path"])
            for f in n["filters"]:
                v = FILTERS[f](v)
            out.append(to_str(v))
        elif k == "each":
            v = lookup(scopes, n["path"])
            items = v if isinstance(v, list) else [{"key": a, "value": b} for a, b in v.items()] if isinstance(v, dict) else []
            shown = items if n["limit"] is None else items[:n["limit"]]
            out.append(n["sep"].join(
                _run(n["body"], [{**(it if isinstance(it, dict) else {}), ".": it, "@index": i}, *scopes])
                for i, it in enumerate(shown)))
        elif k == "if":
            out.append(_run(n["then"] if truthy(lookup(scopes, n["path"])) else n["else"], scopes))
        else:
            v = lookup(scopes, n["path"])
            count = len(v) if isinstance(v, list) else v
            if isinstance(count, str) and D.is_dec(trim(count)):  # runtime numbers are decimal strings ("1"), as JS Number(v)
                count = 1 if D.cmp(trim(count), "1") == 0 else 0
            out.append(_run(n["one"] if count == 1 else n["many"], scopes))
    return "".join(out)


_cache: dict[str, list] = {}


def render(src: str, *scopes: dict) -> str:
    if src not in _cache:
        _cache[src] = parse(src)
    return _run(_cache[src], list(scopes))
