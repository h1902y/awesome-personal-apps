"""osgen AI task shapes and checks (plans/osgen/workflow.md §4, §5), Python reference. Same as runtime/js/checks.ts.

A check returns problems as plain lines the filler can act on ("TB-9999 isn't a ticket in the input").
"""
from __future__ import annotations

import json
import re

import core as C
import dec as D

SENTENCE = re.compile(r"(?<=[.!?])\s+")


def task_shape(spec: dict, step: dict) -> dict:
    """The output fields an AI step returns, with types (what the filler writes, and what `ai:` views read)."""
    t = step["task"]
    if t == "classify":
        return {"key": "text", "label": "enum", "reason": "text"}
    if t == "group":
        return {"group": "text", "key": "text"}
    if t == "pick_quote":
        return {"group": "text", "key": "text", "quote": "text"}
    if t == "extract":
        return {**{f: d["type"] for f, d in step["into"].items()}, "evidence": "text", "line": "int"}
    if t == "draft":
        return {"for": "text", "text": "long_text"}
    if t == "summarize":
        return {"text": "long_text"}
    raise ValueError(f"no task {t}")


def task_rules(spec: dict, steps: list, step: dict) -> list[str]:
    """The limits an AI step's output must meet, in plain words, from its params and its check step (the task file
    shows them, so a filler knows them before the check refuses). Same text as checks.ts taskRules."""
    t = step["task"]
    chk = next((s for s in steps if s.get("type") == "check" and s.get("in") == step["id"]), None)
    kinds = [c["kind"] for c in (chk or {}).get("checks") or []]
    k = input_key(spec, step)
    out = ["a JSON list of rows, each with exactly the fields in `shape`"]
    if t in ("classify", "group", "pick_quote"):
        out.append(f"each `key` is a `{k}` from the inputs")
    if t == "classify":
        labels = step.get("labels") or {}
        out.append("each `label` is one of: " + ", ".join(labels if isinstance(labels, list) else list(labels)))
        if step.get("sparse"):
            out.append("only the rows that match a label; leave the others out")
        if step.get("reason") is not False:
            out.append("ground each `reason` in the row's own text, never in these instructions")  # D-33
    if "one_per_key" in kinds:
        out.append("at most one row per `key`: the strongest label, with the `reason` naming the others" if t == "classify"
                   else "at most one row per key")
    if t == "group":
        out.append("every input row is in exactly one group")
        if step.get("groups"):
            lo, hi = step["groups"]
            out.append(f"{lo}–{hi} groups")
    elif t == "pick_quote":
        tf = step.get("text_field") or "text"
        out.append("exactly one quote per group")
        lo, mw = step.get("min_words") or 5, step.get("max_words")  # D-33
        out.append(f"each `quote` {lo}–{mw} words, copied word for word from the row's `{tf}`" if mw else
                   f"each `quote` at least {lo} words, copied word for word from the row's `{tf}`")
    elif t == "extract":
        out.append("each `line` is a line number from the input")
        out.append("`evidence` copied word for word from line `line`")
        for f in step.get("people") or []:
            out.append(f"`{f}` is a name exactly as written in the input, or null")
    elif t == "draft":
        per = step.get("per")
        field = per if per not in (None, "key", "id") and per != k else k
        out.append(f"one draft per `{field}` in the inputs; `for` is that `{field}`")
        if step.get("lines"):
            lo, hi = step["lines"]
            out.append(f"each `text` has {lo}–{hi} lines, separated by line breaks (\\n)")
    elif t == "summarize":
        if step.get("sentences"):
            lo, hi = step["sentences"]
            out.append(f"the `text` has {lo}–{hi} sentences")
    if "numbers_backed" in kinds:
        out.append("every number in `text` is one the inputs show: never count, sum or round yourself")
    return out


def coerce(shape: dict, value) -> list[dict]:
    """An AI output as the runtime's canonical values: a list of rows with only the shape's fields; numbers as
    decimal strings, text as strings, null kept. Anything that can't be read becomes null (a check then reports it)."""
    items = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        row = {}
        for f, t in shape.items():
            v = it.get(f)
            if v is None:
                row[f] = None
            elif t in NUMERIC:
                row[f] = D.from_(v) if isinstance(v, (int, float, str)) and not isinstance(v, bool) else None
            elif t == "bool":
                row[f] = v if isinstance(v, bool) else None
            else:
                row[f] = v if isinstance(v, str) else json.dumps(v) if isinstance(v, (dict, list)) else str(v)
        out.append(row)
    return out


def _rows(x) -> list[dict]:
    if isinstance(x, list):
        return x
    if isinstance(x, dict) and x.get("kind") == "text":
        return x["lines"]
    return [x] if isinstance(x, dict) else []


def input_key(spec: dict, step: dict) -> str:
    if step["task"] == "extract":
        return "line"
    ins = step["in"] if isinstance(step["in"], list) else [step["in"]]
    first = ins[0]
    if first.startswith("view:"):
        return spec["views"][first[5:]].get("key") or "key"
    return "key"


def _keys(spec, step, inputs) -> set[str]:
    k = input_key(spec, step)
    return {str(r.get(k)) for r in _rows(inputs[0]) if r.get(k) is not None}


def _cited(step: dict, item: dict):
    t = step["task"]
    if t == "draft":
        return item.get("for") if step.get("per") in (None, "key", "id") else None
    if t == "extract":
        return item.get("line")
    return item.get("key")


def _per_item(spec, step, check, item, inputs, keys, by_key) -> list[str]:
    kind = check["kind"]
    t = step["task"]
    out = []
    if kind == "keys_exist":
        c = _cited(step, item)
        if t == "draft" and step.get("per") not in (None, "key", "id") and step.get("per") != input_key(spec, step):
            vals = {str(r.get(step["per"])) for r in _rows(inputs[0]) if r.get(step["per"]) is not None}
            if str(item.get("for")) not in vals:
                out.append(f"{item.get('for')} isn't a {step['per']} in the input")
        elif c is not None and str(c) not in keys:
            out.append(f"{c} isn't a key in the input")
        elif c is None and t in ("classify", "group", "pick_quote", "extract"):
            out.append("an item has no key")
    elif kind == "enum" and t == "classify":
        if item.get("label") not in (step.get("labels") or {}):
            out.append(f"{item.get('key')}: label {item.get('label')!r} isn't one of {', '.join(step.get('labels') or {})}")
    elif kind == "verbatim":
        field = "quote" if t == "pick_quote" else "evidence"
        text_field = step.get("text_field") or "text"
        row = by_key.get(str(_cited(step, item)))
        hay = C.collapse_ws(str((row or {}).get(text_field) or ""))
        needle = C.collapse_ws(str(item.get(field) or ""))
        if not needle or needle not in hay:
            out.append(f"{_cited(step, item)}: {field} {item.get(field)!r} isn't word for word in its {text_field}")
    elif kind == "names_in_input":
        text = " ".join(str(r.get(step.get("text_field") or "text") or "") for r in _rows(inputs[0])).lower()
        for f in step.get("people") or []:
            v = item.get(f)
            if v is not None and not re.search(rf"(?<![A-Za-z0-9]){re.escape(str(v).lower())}(?![A-Za-z0-9])", text):
                out.append(f"line {item.get('line')}: {f} {v!r} isn't a name in the notes")
    elif kind == "length" and t == "pick_quote":  # quote words (D-33): min_words (default 5) to max_words
        lo, hi = step.get("min_words") or 5, step.get("max_words")
        n = len(C.collapse_ws(str(item.get("quote") or "")).split())
        if n < lo or (hi and n > hi):
            out.append(f"{_cited(step, item)}: the quote has {n} words, want {lo}–{hi}" if hi else
                       f"{_cited(step, item)}: the quote has {n} words, want at least {lo}")
    elif kind == "length":
        lo, hi = step.get("sentences") or step.get("lines") or [1, 99]
        text = str(item.get("text") or "").strip()
        n = len([s for s in SENTENCE.split(text) if s.strip()]) if step.get("sentences") else len([l for l in text.split("\n") if l.strip()])
        unit = "sentences" if step.get("sentences") else "lines"
        if not (lo <= n <= hi):
            out.append(f"{item.get('for') or 'the text'}: {n} {unit}, want {lo}–{hi}")
    elif kind == "numbers_backed":
        known = known_numbers(spec, check.get("from") or [], inputs, _views_of(inputs))
        groups = _groups(check.get("groups_from"), _views_of(inputs))
        out += claims(str(item.get("text") or ""), known, groups)
    return out


_VIEWS = {}


def _views_of(inputs):
    return _VIEWS.get("current")


def run_checks(spec: dict, ai_step: dict, check_step: dict, value, inputs: list, views) -> list[str]:
    _VIEWS["current"] = views
    items = _rows(value)
    keys = _keys(spec, ai_step, inputs)
    k = input_key(spec, ai_step)
    by_key = {str(r.get(k)): r for r in _rows(inputs[0])}
    problems: list[str] = []
    for check in check_step.get("checks") or []:
        kind = check["kind"]
        if kind == "covers":
            seen: dict[str, str] = {}
            for it in items:
                key = str(it.get("key"))
                if key in seen and seen[key] != it.get("group"):
                    problems.append(f"{key} is in both {seen[key]} and {it.get('group')}")
                seen[key] = it.get("group")
            missing = sorted(keys - set(seen), key=lambda x: (len(x), x))
            if missing:
                problems.append(f"{len(missing)} in no group: {', '.join(missing[:12])}")
            if ai_step.get("groups"):
                lo, hi = ai_step["groups"]
                n = len({it.get("group") for it in items})
                if not (lo <= n <= hi):
                    problems.append(f"{n} groups, want {lo}–{hi}")
        elif kind == "one_per_key":
            for c, n in _per_key(ai_step, items).items():
                if n > 1:
                    problems.append(f"{c} has {n} labels; give it one: the strongest, with the reason naming the others"
                                    if ai_step["task"] == "classify" else f"{c} has {n} rows, want at most 1")
        elif kind == "one_per_group":
            grouping = inputs[1] if len(inputs) > 1 else []
            want = [g for g in dict.fromkeys(r.get("group") for r in _rows(grouping))]
            got = [it.get("group") for it in items]
            for g in want:
                if got.count(g) != 1:
                    problems.append(f"{g}: {got.count(g)} quotes, want 1")
        else:
            for it in items:
                problems += _per_item(spec, ai_step, check, it, inputs, keys, by_key)
    return list(dict.fromkeys(problems))


def keep_valid(spec: dict, ai_step: dict, check_step: dict, value, inputs: list, views) -> list[dict]:
    _VIEWS["current"] = views
    keys = _keys(spec, ai_step, inputs)
    k = input_key(spec, ai_step)
    by_key = {str(r.get(k)): r for r in _rows(inputs[0])}
    out = []
    for it in _rows(value):
        bad = []
        for check in check_step.get("checks") or []:
            if check["kind"] not in ("covers", "one_per_group", "one_per_key"):
                bad += _per_item(spec, ai_step, check, it, inputs, keys, by_key)
        if not bad:
            out.append(it)
    if any(c["kind"] == "one_per_key" for c in check_step.get("checks") or []):
        out = _strongest(ai_step, out)
    return out


def _strongest(step: dict, items: list[dict]) -> list[dict]:
    """One row per key (D-24, D-32): the one whose label is declared first (labels are listed strongest first); on a
    tie, or for a task without labels, the first. Rows keep their order."""
    labels = step.get("labels") or {}
    order = list(labels if isinstance(labels, list) else labels)
    rank = lambda it: order.index(it.get("label")) if it.get("label") in order else len(order)
    best: dict[str, int] = {}
    for i, it in enumerate(items):
        c = _cited(step, it)
        if c is not None and (str(c) not in best or rank(it) < rank(items[best[str(c)]])):
            best[str(c)] = i
    keep = set(best.values())
    return [it for i, it in enumerate(items) if _cited(step, it) is None or i in keep]


def _per_key(step: dict, items: list) -> dict[str, int]:
    n: dict[str, int] = {}
    for it in items:
        c = _cited(step, it)
        if c is not None:
            n[str(c)] = n.get(str(c), 0) + 1
    return n


# ---------- numbers_backed (A1's checkClaims, generalised) ----------

_MONTH = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
NUMERIC = {"int", "number", "hours", "money"}


def _forms(v: str, t: str) -> set[str]:
    out = {D.from_(v), D.round_(v, 0), D.round_(v, 1), D.round_(D.abs_(v), 1), D.round_(D.abs_(v), 0)}
    if t == "hours":
        days = D.div(D.abs_(v), "24", 1)
        out |= {days, D.round_(days, 0)}
    return {x for x in out if x is not None}


def money_currency(spec: dict, field: str) -> str | None:
    """A money field's currency: the table field of that name, else the spec's one currency (render.py does the same)."""
    for t in spec["tables"].values():
        d = t["fields"].get(field)
        if d and d.get("currency"):
            return d["currency"]
    cur = {d.get("currency") for t in spec["tables"].values() for d in t["fields"].values() if d.get("currency")}
    return cur.pop() if len(cur) == 1 else None


def major(spec: dict, field: str, v: str) -> str:
    """Money in the unit people write (D-20 keeps minor units): 490000 -> 4900 for USD."""
    return v if (money_currency(spec, field) or "").upper() in C.ZERO_DECIMAL else D.div(v, "100", 2)


def known_numbers(spec: dict, names: list[str], inputs, views) -> set[str]:
    known: set[str] = set()
    for ref in names:
        if not ref.startswith("view:") or views is None:
            continue
        name = ref[5:]
        out = spec["views"][name].get("out") or {}
        rows = views.view(name)
        known.add(str(len(rows)))
        for r in rows:
            for f, v in r.items():
                if v is None or f.startswith("_"):
                    continue
                t = out.get(f, "text")
                if t == "money" and isinstance(v, str) and D.is_dec(v):
                    known |= _forms(major(spec, f, v), "number")  # people write 4,900.00, not 490000
                elif t in NUMERIC and isinstance(v, str) and D.is_dec(v):
                    known |= _forms(v, t)
                elif isinstance(v, str):
                    for m in re.finditer(r"-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?", v):
                        known |= _forms(m.group(0).replace(",", ""), "number")
    return known


def _groups(ref, views) -> dict[str, str]:
    if not ref or views is None or not ref.startswith("view:"):
        return {}
    return {str(r.get("group")): str(r.get("count")) for r in views.view(ref[5:]) if r.get("group") is not None and r.get("count") is not None}


def claims(text: str, known: set[str], groups: dict[str, str]) -> list[str]:
    errs = []
    for raw in re.split(r"(?<=[.!?;])\s+|\n+", text):
        s = re.sub(r"\[[^\]]*\]", " ", raw)                                     # [ask: …] gaps
        s = re.sub(r"\b[A-Z]{1,6}-[0-9]+\b", " ", s)                           # ticket ids
        s = re.sub(r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b", " ", s)
        s = re.sub(rf"\b[0-9]{{1,2}}\s+(?:{_MONTH})[a-z]*\.?(?:\s+[0-9]{{4}})?", " ", s, flags=re.I)
        s = re.sub(rf"\b(?:{_MONTH})[a-z]*\.?\s+[0-9]{{1,2}}(?:,?\s+[0-9]{{4}})?", " ", s, flags=re.I)
        s = re.sub(r"\b[0-9]{1,2}:[0-9]{2}\b", " ", s)
        s = re.sub(r"\b(?:19|20)[0-9]{2}\b", " ", s)
        found = list(re.finditer(r"((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)(\s*%|[\s-]*(?:h\b|hrs?\b|hours?\b|d\b|days?\b))?", s, re.I))
        counts = [D.from_(m.group(1).replace(",", "")) for m in found if not m.group(2)]  # 4,900.00 is one number
        for m in found:
            n = D.from_(m.group(1).replace(",", ""))
            if n not in known:
                errs.append(f"\"{m.group(0).strip()}\" isn't a number the app's data backs: {raw.strip()[:120]}")
        for name, count in groups.items():
            if counts and re.search(rf"\b{re.escape(name)}\b", s, re.I) and D.from_(count) not in counts:
                errs.append(f"{name} has {count}, the text says {', '.join(counts)}: {raw.strip()[:120]}")
    return list(dict.fromkeys(errs))
