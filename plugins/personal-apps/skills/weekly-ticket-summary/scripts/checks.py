"""osgen AI task shapes and checks (plans/osgen/workflow.md §4, §5), Python reference. Same as runtime/js/checks.ts.

A check returns problems as plain lines the filler can act on ("TB-9999 isn't a ticket in the input").
"""
from __future__ import annotations

import json
import re

import core as C
import dec as D

SENTENCE = re.compile(r"(?<=[.!?])\s+")
WORD = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*")  # a word for "word for word": letters and digits, apostrophes inside


def _in_words(hay: str, needle: str) -> bool:
    """`needle`'s words appear in `hay` in a row, word for word (spacing and punctuation aside)."""
    h, n = WORD.findall(hay), WORD.findall(needle)
    return bool(n) and any(h[i:i + len(n)] == n for i in range(len(h) - len(n) + 1))


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
        out.append(f"each `quote` {lo}–{mw} words (or the row's whole `{tf}` when it's shorter), copied word for word from the row's `{tf}`"
                   if mw else f"each `quote` at least {lo} words (or the row's whole `{tf}`), copied word for word from the row's `{tf}`")
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
        # the 1.2.3 wording: Llama wrote field names for "a field's name written out" and spelled numbers out for
        # "said with the plain words"; the check itself ties each number to what it sits beside
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


def _draft_field(spec, step) -> str:
    """The input field a draft step writes one draft per (`per`, or the input's key)."""
    per = step.get("per")
    return per if per not in (None, "key", "id") else input_key(spec, step)


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
        if not _in_words(str((row or {}).get(text_field) or ""), str(item.get(field) or "")):
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
        whole = WORD.findall(str((by_key.get(str(_cited(step, item))) or {}).get(step.get("text_field") or "text") or ""))
        if (n < lo and not (whole and WORD.findall(str(item.get("quote") or "")) == whole)) or (hi and n > hi):  # a short message whole
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
        own = (_draft_field(spec, step), item.get("for")) if t == "draft" else None
        fs = facts(spec, check.get("from") or [], _views_of(inputs), own)
        groups = _groups(check.get("groups_from"), _views_of(inputs))
        out += claims(str(item.get("text") or ""), fs, groups)
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
        elif kind == "keys_exist" and ai_step["task"] == "draft":  # one draft per key: none missing, none twice
            problems += _one_draft_each(spec, ai_step, items, inputs)
            for it in items:
                problems += _per_item(spec, ai_step, check, it, inputs, keys, by_key)
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
    if ai_step["task"] == "draft":  # one draft per key: the first valid one
        seen: set[str] = set()
        out = [it for it in out if not (str(it.get("for")) in seen or seen.add(str(it.get("for"))))]
    return out


def _one_draft_each(spec: dict, step: dict, items: list, inputs: list) -> list[str]:
    field = _draft_field(spec, step)
    want = list(dict.fromkeys(str(r.get(field)) for r in _rows(inputs[0]) if r.get(field) is not None))
    got: dict[str, int] = {}
    for it in items:
        got[str(it.get("for"))] = got.get(str(it.get("for")), 0) + 1
    out = []
    missing = [w for w in want if w not in got]
    if missing:
        out.append(f"{len(missing)} of {len(want)} have no draft (one per `{field}`): {', '.join(missing[:12])}")
    out += [f"{k} has {n} drafts, want 1" for k, n in got.items() if n > 1 and k in want]
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


# Every number is tied to what it sits beside (B-134: "9 tickets are urgent" passed on 10 urgent, because a 9 h wait
# and a 9% tax were in the inputs). A number passes only when something named beside it has that value: in its clause, a
# field, by its name or a page label ("urgent", "Reply first", "over target"), or a view's row count, by the view's
# name; in its sentence, a row, by its key or name ("TB-4212", "Bank sync"; a draft's own rows always), whose
# numbers, text included, back it.
# A field of many rows named without its row ("tickets" for a theme's count) backs only an hour or day amount, or a
# number in a clause that names nothing more exact. An amount with a unit (a currency, h or d, %) may also be any value
# of a field of that kind (money; hours, or a field named in days; a decimal): never a count, never free text.

_STOP = {"a", "an", "the", "of", "in", "on", "at", "to", "for", "by", "per", "and", "or", "is", "are", "with", "from", "id"}
_GENERIC = {"count", "row", "list", "total", "brief", "base", "now"}  # view-name words that don't say what's counted
_TOKEN = re.compile(r"[A-Za-z0-9]+")
_NUM_IN_TEXT = re.compile(r"-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?")
_CLAUSE = re.compile(r"\s+(?:and|but|while|whereas)\s+|\s+[-\u2013\u2014]\s+", re.I)
_GAP = re.compile(r"\[[^\]]*\]")


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def name_words(name: str, generic: set[str] = frozenset()) -> list[str]:
    """The words that name a field, label or view, as stems ("over_target" -> over, target; ASCII letters only)."""
    out = []
    for tok in _TOKEN.findall(name):
        w = tok.lower()
        if w in _STOP or w.isdigit() or len(w) < 2 or _stem(w) in generic:
            continue
        out.append(_stem(w))
    return out


def _word_match(f: str, t: str) -> bool:
    return f == t or (min(len(f), len(t)) >= 4 and (t.startswith(f) or f.startswith(t)))


def _named(words: list[str], toks: list[str]) -> bool:
    return bool(words) and all(any(_word_match(f, t) for t in toks) for f in words)


def page_labels(spec: dict) -> dict[str, list[str]]:
    """The labels pages give a field (stat labels, table column labels), by field name."""
    out: dict[str, list[str]] = {}

    def walk(o):
        if isinstance(o, dict):
            f = o.get("value") if isinstance(o.get("value"), str) else o.get("field") if isinstance(o.get("field"), str) else None
            if f is not None and isinstance(o.get("label"), str) and o["label"] not in out.setdefault(f, []):
                out[f].append(o["label"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(spec.get("pages") or [])
    return out


def _value_forms(spec: dict, f: str, t: str, v) -> set[str]:
    if not isinstance(v, str) or not D.is_dec(v):
        return set()
    if t == "money":
        return _forms(major(spec, f, v), "number")  # people write 4,900.00, not 490000
    return _forms(v, t) if t in NUMERIC else set()


def _kind(f: str, ty: str) -> str | None:
    """The unit an amount of this field is written with: money, hours (or days), pct (a decimal); a count has none."""
    if ty == "money":
        return "money"
    if ty == "hours" or any(_stem(w.lower()) in ("day", "hour") for w in _TOKEN.findall(f)):
        return "hours"
    return "pct" if ty == "number" else None


def _ident_word(v: str) -> str | None:
    """A row name's first word that names the row alone ("SLACK T03TBIRD" -> slack, "Bank sync" -> bank)."""
    for tok in _TOKEN.findall(v):
        w = tok.lower()
        if w not in _STOP and len(w) >= 4 and re.search(r"[a-z]", w):
            return _stem(w)
    return None


def source(spec: dict, name: str, types: dict, rows: list, key, labels: dict, own=None) -> dict:
    """One view (or table) as what claims ties numbers to: its row count, its numeric fields and its rows."""
    ident = key or next((f for f, ty in types.items() if ty == "text"), None)
    fields = []
    kinds: dict[str, set[str]] = {"money": set(), "hours": set(), "pct": set()}
    for f, ty in types.items():
        if f.startswith("_") or ty not in NUMERIC:
            continue
        if _kind(f, ty):
            for r in rows:
                kinds[_kind(f, ty)] |= _value_forms(spec, f, ty, r.get(f))
        names = [f] + labels.get(f, [])
        nums = {D.from_(x) for n in names for x in _TOKEN.findall(n) if x.isdigit()}
        shown = None
        if len(rows) == 1 and isinstance(rows[0].get(f), str) and D.is_dec(rows[0][f]):
            shown = D.from_(major(spec, f, rows[0][f]) if ty == "money" else rows[0][f])
        fields.append({"name": f.replace("_", " "), "words": [w for w in (name_words(n) for n in names) if w],
                       "nums": sorted(nums), "values": [sorted(_value_forms(spec, f, ty, r.get(f))) for r in rows], "shown": shown})
    out_rows = []
    for r in rows:
        vals: set[str] = set()
        for f, v in r.items():
            if v is None or f.startswith("_"):
                continue
            ty = types.get(f, "text")
            if ty in NUMERIC:
                vals |= _value_forms(spec, f, ty, v)
            elif isinstance(v, str):
                for m in _NUM_IN_TEXT.finditer(v):
                    vals |= _forms(m.group(0).replace(",", ""), "number")
        v = r.get(ident) if ident else None
        spans = [v] if isinstance(v, str) and re.search(r"[A-Za-z]", v) else []
        mine = own is not None and own[1] is not None and r.get(own[0]) is not None and str(r.get(own[0])) == str(own[1])
        out_rows.append({"spans": spans, "word": _ident_word(v) if spans else None, "own": mine, "values": sorted(vals)})
    return {"name": name.replace("_", " "), "words": name_words(name, _GENERIC), "count": str(len(rows)),
            "fields": fields, "rows": out_rows, "kinds": {k: sorted(v) for k, v in kinds.items()}}


def facts(spec: dict, names: list[str], views, own=None) -> list[dict]:
    """What numbers_backed ties a text's numbers to: the `from` views at the period the check runs. own: a draft's
    (field, value); the rows with that value back its numbers wherever they sit."""
    if views is None:
        return []
    labels = page_labels(spec)
    # the app's item nouns ("ticket"): naming only these ties a number weakly (a theme's count check still applies)
    nouns = sorted({w for name, tb in (spec.get("tables") or {}).items() for w in name_words(name) + name_words(str(tb.get("label") or ""))})
    out = []
    for ref in names:
        if ref.startswith("view:"):
            v = spec["views"][ref[5:]]
            out.append({**source(spec, ref[5:], v.get("out") or {}, views.view(ref[5:]), v.get("key"), labels, own), "nouns": nouns})
    return out


def _groups(ref, views) -> dict[str, str]:
    if not ref or views is None or not ref.startswith("view:"):
        return {}
    return {str(r.get("group")): str(r.get("count")) for r in views.view(ref[5:]) if r.get("group") is not None and r.get("count") is not None}


def _clauses(sentence: str, protect: list[str]) -> list[str]:
    """A sentence cut at "and", "but", "while", "whereas" and spaced dashes, never inside a gap or a protected name
    ("Billing and refunds")."""
    spans = [(m.start(), m.end()) for m in _GAP.finditer(sentence)]
    for name in protect:
        spans += [(m.start(), m.end()) for m in re.finditer(rf"\b{re.escape(name)}\b", sentence, re.I)]
    out, at = [], 0
    for m in _CLAUSE.finditer(sentence):
        if not any(m.start() < e and s < m.end() for s, e in spans):
            out.append(sentence[at:m.start()])
            at = m.end()
    return out + [sentence[at:]]


def _rows_named(sentence: str, fs: list[dict]) -> list[list[int]]:
    """Per source, the rows a sentence names (by key, name or the name's first word), with a draft's own rows."""
    toks = {_stem(x.lower()) for x in _TOKEN.findall(sentence)}
    return [[i for i, r in enumerate(src["rows"]) if r["own"] or r.get("word") in toks
             or any(re.search(rf"\b{re.escape(s)}\b", sentence, re.I) for s in r["spans"])] for src in fs]


def _unit(m, s: str) -> str | None:
    """A found number's unit: pct, hours (h or d), money (a currency code or sign beside it), or None for a count."""
    u = (m.group(2) or "").strip()
    if u:
        return "pct" if u == "%" or u.lower().startswith("per") else "hours"
    if re.match(r"\s*[A-Z]{3}(?!\w)", s[m.end():]) or re.search(r"(?:[$\u20ac\u00a3\u00a5\u20b9]|(?<!\w)[A-Z]{3})\s*$", s[:m.start()]):
        return "money"
    return None


def _beside(clause: str, fs: list[dict], named_rows: list[list[int]]) -> tuple[set[str], set[str], list[str], set[str]]:
    """The values named beside a clause's numbers, exact and vague (a field of many rows without its row), what names
    them, and the strong ones: exact, named by more than the app's item noun ("over target", not just "tickets")."""
    toks = [_stem(x.lower()) for x in _TOKEN.findall(_GAP.sub(" ", clause))]
    vals: set[str] = set()
    vague: set[str] = set()
    strong: set[str] = set()
    named: list[str] = []
    for src, rows in zip(fs, named_rows):
        nouns = set(src.get("nouns") or [])
        if _named(src["words"], toks):
            vals.add(src["count"])
            named.append(f"{src['name']} ({src['count']})")
            if set(src["words"]) - nouns:
                strong.add(src["count"])
        for fld in src["fields"]:
            hit = [w for w in fld["words"] if _named(w, toks)]
            if hit:
                for i in rows or range(len(fld["values"])):
                    exact = bool(rows) or len(fld["values"]) == 1
                    (vals if exact else vague).update(fld["values"][i])
                    if exact and any(set(w) - nouns for w in hit):
                        strong.update(fld["values"][i])
                vals |= set(fld["nums"])
                named.append(f"{fld['name']} ({fld['shown']})" if fld["shown"] is not None else
                             fld["name"] if len(fld["values"]) == 1 else f"{fld['name']} in {src['name']}")
        for i in rows:
            vals |= set(src["rows"][i]["values"])
            strong |= set(src["rows"][i]["values"])
            named += src["rows"][i]["spans"][:1]
    return vals, vague, list(dict.fromkeys(named)), strong


def _masked(s: str) -> str:
    s = _GAP.sub(" ", s)                                                       # [ask: …] gaps
    s = re.sub(r"\b[A-Z]{1,6}-[0-9]+\b", " ", s)                               # ticket ids
    s = re.sub(r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b", " ", s)
    s = re.sub(rf"\b[0-9]{{1,2}}\s+(?:{_MONTH})[a-z]*\.?(?:\s+[0-9]{{4}})?", " ", s, flags=re.I)
    s = re.sub(rf"\b(?:{_MONTH})[a-z]*\.?\s+[0-9]{{1,2}}(?:,?\s+[0-9]{{4}})?", " ", s, flags=re.I)
    s = re.sub(r"\b[0-9]{1,2}:[0-9]{2}\b", " ", s)
    return re.sub(r"\b(?:19|20)[0-9]{2}\b", " ", s)


def _numbers(s: str) -> list:
    return list(re.finditer(r"((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)(\s*%|\s+per\s*cent\b|[\s-]*(?:h\b|hrs?\b|hours?\b|d\b|days?\b))?", s, re.I))


def claims(text: str, fs: list[dict], groups: dict[str, str]) -> list[str]:
    errs = []
    protect = [p for p in list(groups) + [s for src in fs for r in src["rows"] for s in r["spans"]] if p]
    for raw in re.split(r"(?<=[.!?;])\s+|\n+", text):
        shown = raw.strip()[:120]
        s = _masked(raw)
        found = _numbers(s)
        if not found:
            continue
        rows = _rows_named(raw, fs)
        for clause in _clauses(raw, protect):
            cs = _masked(clause)
            nums = _numbers(cs)
            if not nums:
                continue
            vals, vague, named, _ = _beside(clause, fs, rows)
            for m in nums:
                n = D.from_(m.group(1).replace(",", ""))
                u = _unit(m, cs)
                if n in vals or (n in vague and (not vals or u == "hours")) or (u and any(n in (src.get("kinds") or {}).get(u, []) for src in fs)):
                    continue
                if named:
                    errs.append(f"\"{m.group(0).strip()}\" doesn't match what it sits beside ({', '.join(named[:4])}): {shown}")
                else:
                    errs.append(f"\"{m.group(0).strip()}\" isn't tied to anything the app counts; name what it counts beside it: {shown}")
            # a theme's count, in a clause that names that one theme ("Sync with 8, … and Billing and Invoices are close behind")
            counts = [D.from_(m.group(1).replace(",", "")) for m in nums if not m.group(2)]  # 4,900.00 is one number
            names = [g for g in groups if g and re.search(rf"\b{re.escape(g)}\b", cs, re.I)]
            if counts and len(names) == 1 and D.from_(groups[names[0]]) not in counts:
                # a number something else beside it names strongly isn't the theme's ("4 tickets over their target, with
                # themes including Billing"); "Billing has 40 tickets" still is (only the item noun names the 40)
                rest = [[i for i in rs if names[0] not in src["rows"][i]["spans"]] for src, rs in zip(fs, rows)]
                left = [c for c in counts if c not in _beside(clause, fs, rest)[3]]
                if left:
                    errs.append(f"{names[0]} has {groups[names[0]]}, the text says {', '.join(left)}: {shown}")
    return list(dict.fromkeys(errs))
