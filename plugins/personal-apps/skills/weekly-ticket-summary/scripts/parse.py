"""osgen field parsing (plans/osgen/tables.md §2): text cell -> canonical value for each field type, as `validate`
does it. Same code as runtime/js/parse.ts. Returns (value, ok): ok is False when the text was there but didn't
parse (a cell problem); an empty cell is (None, True).

Leniency kept from A1 v1.1.0 (open with B-130, Q2): hours and numbers take a decimal comma ("3,5"); dates also
read "Oct 21, 2026", "21 Oct 2026" and "21.10.2026"; slashes follow the field's date_order (default dmy_then_mdy), else
D/M/Y if that's a valid date and M/D/Y otherwise (A1's order).
"""
from __future__ import annotations

import re

import core as C
import dec as D
from table import MONTHS, date_utc, trim

_D = r"[0-9]"


def _dec_text(v: str, dc: bool | None = None) -> str | None:
    """'1,204.50' / '3,5' / '-2' / '12' -> canonical decimal, else None. `dc` is the field's decimal_comma: None (A1
    v1.1.0, the default) reads ',' as thousands in 1,204.50 and as the decimal point in 3,5; True reads 1.204,50 and
    3,5; False reads only a decimal point."""
    v = v.replace(" ", "")
    if dc is True:
        if re.fullmatch(rf"-?{_D}{{1,3}}(\.{_D}{{3}})+(,{_D}+)?", v):
            v = v.replace(".", "")
        if re.fullmatch(rf"-?{_D}+(,{_D}+)?", v):
            v = v.replace(",", ".")
        elif not re.fullmatch(rf"-?{_D}+", v):
            return None
    elif re.fullmatch(rf"-?{_D}{{1,3}}(,{_D}{{3}})+(\.{_D}+)?", v):
        v = v.replace(",", "")
    elif dc is None and re.fullmatch(rf"-?{_D}+,{_D}+", v):
        v = v.replace(",", ".")
    if not re.fullmatch(rf"-?{_D}+(\.{_D}+)?", v):
        return None
    return D.from_(v)


def parse_int(v: str, dc: bool | None = None):
    d = _dec_text(v, dc)
    if d is None or "." in d:
        return None
    return d


def parse_number(v: str, dc: bool | None = None):
    return _dec_text(v, dc)


_UNIT = {"h": "1", "hr": "1", "hrs": "1", "hour": "1", "hours": "1", "d": "24", "day": "24", "days": "24",
         "m": "m", "min": "m", "mins": "m", "minute": "m", "minutes": "m"}


def parse_hours(v: str, dc: bool | None = None):
    s = trim(v).lower()
    if not s:
        return None
    m = re.fullmatch(rf"({_D}{{1,3}}):({_D}{{2}})", s)
    if m:
        return D.add(m.group(1), D.div(m.group(2), "60"))
    parts = re.findall(rf"(-?{_D}+(?:[.,]{_D}+)?)\s*([a-z]*)", s)
    if not parts or re.sub(rf"(-?{_D}+(?:[.,]{_D}+)?)\s*([a-z]*)", "", s).strip():
        return None
    total = "0"
    for n, unit in parts:
        if "," in n and dc is False:
            return None
        n = D.from_(n.replace(",", "."))
        u = _UNIT.get(unit or "h")
        if n is None or u is None:
            return None
        total = D.add(total, D.div(n, "60") if u == "m" else D.mul(n, u))
    return total


def parse_money(v: str, currency: str, dc: bool | None = None):
    s = trim(v)
    if not s:
        return None
    neg = False
    if re.fullmatch(r"\(.*\)", s):
        neg, s = True, s[1:-1]
    code = re.search(r"\b([A-Z]{3})\b", s)
    if code and code.group(1) != currency:
        return None
    s = re.sub(r"[A-Z]{3}|[$€£¥₹\s]", "", s)
    d = _dec_text(s, dc)
    if d is None:
        return None
    minor = D.round_(D.mul(d, "1" if currency in C.ZERO_DECIMAL else "100"), 0)
    return D.neg(minor) if neg else minor


def parse_bool(v: str):
    k = C.norm_key(v)
    if k in ("true", "yes", "y", "1"):
        return True
    if k in ("false", "no", "n", "0"):
        return False
    return None


def _ymd(y, m, d) -> str | None:
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    t = date_utc(y, m - 1, d)
    iso = C.iso_date(t)
    return iso if iso == f"{y:04d}-{m:02d}-{d:02d}" else None


def _date_parts(s: str, order: str = "dmy_then_mdy"):
    """(y, m, d, rest) from the date shapes A1 reads; rest is the time part, or None."""
    m = re.fullmatch(rf"({_D}{{4}})-({_D}{{2}})-({_D}{{2}})(?:[T ](.*))?", s)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    m = re.fullmatch(rf"({_D}{{1,2}})/({_D}{{1,2}})/({_D}{{4}})(?: (.*))?", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if order == "iso":
            return None
        if order == "dmy" or (order == "dmy_then_mdy" and b <= 12):
            return y, b, a, m.group(4)
        return y, a, b, m.group(4)
    m = re.fullmatch(rf"({_D}{{1,2}})\.({_D}{{1,2}})\.({_D}{{4}})(?: (.*))?", s)
    if m:
        return int(m.group(3)), int(m.group(2)), int(m.group(1)), m.group(4)
    m = re.fullmatch(rf"([A-Za-z]{{3}}) ({_D}{{1,2}}), ({_D}{{4}})(?: (.*))?", s)
    if m and m.group(1).lower() in MONTHS:
        return int(m.group(3)), MONTHS.index(m.group(1).lower()) + 1, int(m.group(2)), m.group(4)
    m = re.fullmatch(rf"({_D}{{1,2}}) ([A-Za-z]{{3}}) ({_D}{{4}})(?: (.*))?", s)
    if m and m.group(2).lower() in MONTHS:
        return int(m.group(3)), MONTHS.index(m.group(2).lower()) + 1, int(m.group(1)), m.group(4)
    return None


def parse_date(v: str, order: str = "dmy_then_mdy"):
    s = trim(v)
    if not s:
        return None
    p = _date_parts(s, order)
    if not p or p[3]:
        return None
    return _ymd(p[0], p[1], p[2])


def parse_datetime(v: str, order: str = "dmy_then_mdy"):
    """No zone = UTC (the spec's timezone; only UTC in v1)."""
    s = trim(v)
    if not s:
        return None
    p = _date_parts(s, order)
    if not p:
        return None
    day = _ymd(p[0], p[1], p[2])
    if day is None:
        return None
    rest = (p[3] or "").strip()
    h = mi = sec = ms = 0
    off = 0
    if rest:
        m = re.fullmatch(rf"({_D}{{1,2}}):({_D}{{2}})(?::({_D}{{2}})(?:\.({_D}{{1,3}}){_D}*)?)?\s*(Z|[+-]{_D}{{2}}:?{_D}{{2}})?", rest)
        if not m:
            return None
        h, mi = int(m.group(1)), int(m.group(2))
        sec = int(m.group(3) or 0)
        ms = int((m.group(4) or "0").ljust(3, "0"))
        tz = m.group(5)
        if h > 23 or mi > 59 or sec > 59:
            return None
        if tz and tz != "Z":
            sign = -1 if tz[0] == "-" else 1
            digits = tz[1:].replace(":", "")
            off = sign * (int(digits[:2]) * 60 + int(digits[2:])) * 60000
    t = C.ms(day) + ((h * 60 + mi) * 60 + sec) * 1000 + ms - off
    return C.iso_datetime(t)


def parse_enum(v: str, values: list[str], allow_other: bool):
    s = trim(v)
    if not s:
        return None
    k = C.norm_key(s)
    for x in values:
        if C.norm_key(x) == k:
            return x
    return "other" if allow_other else None


def parse_field(field: dict, raw: str):
    """(value, ok) for one cell."""
    t = field["type"]
    s = raw if t == "long_text" else trim(raw)
    if s == "" or (t != "long_text" and trim(s) == ""):
        return None, True
    if t == "text":
        return s, True
    if t == "long_text":
        return trim(s), True
    dc = field.get("decimal_comma")
    order = field.get("date_order") or {True: "dmy", False: "mdy"}.get(field.get("day_first"), "dmy_then_mdy")
    if t == "int":
        v = parse_int(s, dc)
    elif t == "number":
        v = parse_number(s, dc)
    elif t == "hours":
        v = parse_hours(s, dc)
    elif t == "money":
        v = parse_money(s, field["currency"], dc)
    elif t == "bool":
        v = parse_bool(s)
    elif t == "date":
        v = parse_date(s, order)
    elif t == "datetime":
        v = parse_datetime(s, order)
    elif t == "enum":
        v = parse_enum(s, field["values"], bool(field.get("allow_other")))
    elif t == "ref":
        return s, True  # resolved against the target's key by `save`
    else:
        return None, False
    return v, v is not None
