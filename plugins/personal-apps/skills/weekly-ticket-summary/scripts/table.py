"""osgen engine, table layer, in Python (stdlib only): the same code as runtime/js/table.ts, line for line where it
matters, so the agent target's script and the OS give the same numbers. tests/parity.mjs runs both and compares.

Differences from Python's own helpers are on purpose: strip() uses JavaScript's whitespace set, dates are built like
Date.UTC (out-of-range days roll over), and CSV is read by the same RFC 4180 loop (not csv.Sniffer).
"""
from __future__ import annotations

import re
from decimal import Decimal

# String.prototype.trim()'s set (WhiteSpace + LineTerminator), not str.strip()'s.
_WS = " \t\n\v\f\r                 　﻿"


def trim(s: str) -> str:
    return s.strip(_WS)


def norm(h: str) -> str:
    return re.sub(r"^_+|_+$", "", re.sub(r"[^a-z0-9]+", "_", trim(h).lower()))


def _to_fixed12(x: float) -> str:
    return format(x, ".12f")


def half_up(x: float, places: int = 1) -> float:
    """Round half up on the decimal string, as runtime/js/table.ts halfUp."""
    neg = x < 0
    s = _to_fixed12(abs(x)).rstrip("0")
    int_part, _, frac = s.partition(".")
    keep = frac[:places].ljust(places, "0")
    scaled = int(int_part + keep)
    if (frac[places] if len(frac) > places else "0") >= "5":
        scaled += 1
    v = scaled / 10 ** places
    return -v if neg and v != 0 else v


def js_str(x) -> str:
    """String(x) for a JS number: 166 not 166.0, 17.5, 1e21 and beyond as JS writes them (rare here)."""
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    if x != x:
        return "NaN"
    if x in (float("inf"), float("-inf")):
        return "Infinity" if x > 0 else "-Infinity"
    if x == int(x) and abs(x) < 1e21:
        return str(int(x))
    r = repr(x)
    if "e" in r:
        m, e = r.split("e")
        e = int(e)
        if -7 < e < 21:
            return format(Decimal(r), "f")
        return f"{m}e{'+' if e > 0 else '-'}{abs(e)}"
    return r


def num(x: float) -> str:
    return js_str(half_up(x))


def fmt_wait(h: float | None) -> str:
    if h is None:
        return "unknown"
    if h < 48:
        return f"{num(h)} h"
    return f"{half_up(h / 24):.1f} d"


_HOURS = re.compile(r"^(-?[0-9]+(?:\.[0-9]+)?)\s*(h|hr|hrs|hour|hours|d|day|days|m|min|mins|minutes)?$")


def parse_hours(value: str) -> float | None:
    v = trim(value).lower().replace(",", ".")
    m = _HOURS.match(v)
    if not m or m.end() != len(v):
        return None
    n = float(m.group(1))
    unit = m.group(2) or "h"
    return n * 24 if unit.startswith("d") else n / 60 if unit.startswith("m") else n


def parse_number(value: str) -> float | None:
    v = trim(value)
    if not v:
        return None
    neg = False
    if re.fullmatch(r"\(.*\)", v):
        neg, v = True, v[1:-1]
    v = re.sub(r"[\s$€£¥₹]|[A-Za-z]{3}\Z|^[A-Za-z]{3}", "", v)
    if re.fullmatch(r"-?[0-9]{1,3}(,[0-9]{3})+(\.[0-9]+)?", v):
        v = v.replace(",", "")
    elif re.fullmatch(r"-?[0-9]+,[0-9]{1,2}", v):
        v = v.replace(",", ".", 1)
    if not re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", v):
        return None
    n = float(v)
    return -n if neg else n


def parse_bool(value: str) -> bool | None:
    v = norm(value)
    if v in ("yes", "y", "true", "1", "x", "attached", "on_file"):
        return True
    if v in ("no", "n", "false", "0", "missing", "none", ""):
        return None if v == "" else False
    return None


def _days_from_civil(y: int, m: int, d: int) -> int:
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def date_utc(y: int, mo: int, d: int = 1, h: int = 0, mi: int = 0, s: int = 0) -> int:
    """Date.UTC(y, mo, d, h, mi, s) in ms; mo is 0-based and every part may overflow."""
    y += mo // 12
    mo %= 12
    days = _days_from_civil(y, mo + 1, 1) + d - 1
    return (((days * 24 + h) * 60 + mi) * 60 + s) * 1000


MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def parse_date(value: str) -> int | None:
    v = trim(value)
    if not v:
        return None
    m = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[T ]([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.[0-9]+)?)?)?\s*(Z|[+-][0-9]{2}:?[0-9]{2})?", v)
    if m:
        y, mo, d, hh, mi, ss, tz = m.groups()
        t = date_utc(int(y), int(mo) - 1, int(d), int(hh or 0), int(mi or 0), int(ss or 0))
        if tz and tz != "Z":
            sign = -1 if tz[0] == "-" else 1
            digits = tz[1:].replace(":", "")
            t -= sign * (int(digits[:2]) * 60 + int(digits[2:] or 0)) * 60000
        return t

    def hm(s):
        return [int(x) for x in s.split(":")] if s else [0, 0]

    m = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})(?: ([0-9]{1,2}:[0-9]{2}))?", v)
    if m:
        h, mi = hm(m.group(4))
        a, b = int(m.group(1)), int(m.group(2))
        if b <= 12:
            return date_utc(int(m.group(3)), b - 1, a, h, mi)
        if a <= 12:
            return date_utc(int(m.group(3)), a - 1, b, h, mi)
        return None
    m = re.fullmatch(r"([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{4})(?: ([0-9]{1,2}:[0-9]{2}))?", v)
    if m:
        h, mi = hm(m.group(4))
        return date_utc(int(m.group(3)), int(m.group(2)) - 1, int(m.group(1)), h, mi)
    m = re.fullmatch(r"([A-Za-z]{3}) ([0-9]{1,2}), ([0-9]{4})(?: ([0-9]{1,2}:[0-9]{2}))?", v)
    if m and m.group(1).lower() in MONTHS:
        h, mi = hm(m.group(4))
        return date_utc(int(m.group(3)), MONTHS.index(m.group(1).lower()), int(m.group(2)), h, mi)
    m = re.fullmatch(r"([0-9]{1,2}) ([A-Za-z]{3}) ([0-9]{4})(?: ([0-9]{1,2}:[0-9]{2}))?", v)
    if m and m.group(2).lower() in MONTHS:
        h, mi = hm(m.group(4))
        return date_utc(int(m.group(3)), MONTHS.index(m.group(2).lower()), int(m.group(1)), h, mi)
    return None


def _sniff(sample: str) -> str:
    first = re.split(r"\r?\n", sample)[0] if sample else ""
    best, count = ",", 0
    for d in [",", ";", "\t", "|"]:
        n = first.count(d)
        if n > count:
            best, count = d, n
    return best


def parse_csv(text: str, delimiter: str | None = None) -> list[list[str]]:
    if text[:1] == "﻿":
        text = text[1:]
    d = delimiter or _sniff(text[:4096])
    rows: list[list[str]] = []
    row: list[str] = []
    field: list[str] = []
    quoted = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quoted:
            if c == '"':
                if i + 1 < n and text[i + 1] == '"':
                    field.append('"')
                    i += 1
                else:
                    quoted = False
            else:
                field.append(c)
        elif c == '"' and not field:
            quoted = True
        elif c == d:
            row.append("".join(field))
            field = []
        elif c in "\n\r":
            if c == "\r" and i + 1 < n and text[i + 1] == "\n":
                i += 1
            row.append("".join(field))
            rows.append(row)
            row, field = [], []
        else:
            field.append(c)
        i += 1
    if field or row:
        row.append("".join(field))
        rows.append(row)
    return [r for r in rows if any(trim(c) for c in r)]


def map_columns(headers: list[str], fields: list[dict], overrides: dict | None = None) -> dict[str, str]:
    by_norm: dict[str, str] = {}
    for h in headers:
        by_norm.setdefault(norm(h), h)
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for field, col in (overrides or {}).items():
        if not col:
            continue
        hit = col if col in headers else by_norm.get(norm(col))
        if hit is None:
            raise ValueError(f'No column "{col}" for {field} (columns: {", ".join(headers)})')
        mapping[field] = hit
        used.add(hit)
    for f in fields:
        names = f.get("match") or []
        if f["id"] in mapping or not names:
            continue
        hit = next((by_norm[n] for n in names if n in by_norm and by_norm[n] not in used), None)
        if hit is None:
            for n in names:
                if len(n) <= 3:
                    continue
                hit = next((h for h in headers if h not in used and n in norm(h)), None)
                if hit is not None:
                    break
        if hit is not None:
            mapping[f["id"]] = hit
            used.add(hit)
    return mapping


def read_rows(text: str) -> tuple[list[str], list[list[str]], int]:
    all_rows = parse_csv(text)
    if not all_rows:
        return [], [], 0
    headers = [trim(h) for h in all_rows[0]]
    rows, skipped = [], 0
    for r in all_rows[1:]:
        while len(r) > len(headers) and not trim(r[-1]):
            r = r[:-1]
        if len(r) != len(headers):
            skipped += 1
            continue
        rows.append([trim(c) for c in r])
    return headers, rows, skipped


def typed(type_: str, raw: str):
    if type_ in ("number", "money"):
        return parse_number(raw)
    if type_ == "hours":
        return parse_hours(raw)
    if type_ == "datetime":
        return parse_date(raw)
    if type_ == "bool":
        return parse_bool(raw)
    return raw


def table(text: str, fields: list[dict], overrides: dict | None = None) -> dict:
    headers, rows, skipped = read_rows(text)
    if not headers:
        return {"headers": headers, "mapping": {}, "rows": [], "skipped": 0, "error": "the file has no rows"}
    mapping = map_columns(headers, fields, overrides)
    at = {f: headers.index(h) for f, h in mapping.items()}
    out = []
    for i, r in enumerate(rows):
        row = {"_n": i}
        for f in fields:
            fid = f["id"]
            if fid in at:
                row[fid] = typed(f["type"], r[at[fid]])
            else:
                row[fid] = "" if f["type"] in ("id", "text", "enum") else None
        out.append(row)
    return {"headers": headers, "mapping": mapping, "rows": out, "skipped": skipped}
