"""osgen core functions in Python: the same code as runtime/js/core.ts (see there for the value rules)."""
from __future__ import annotations

import re

import dec as D
from table import _days_from_civil, date_utc, trim


def norm_key(s: str) -> str:
    return re.sub(r"^_+|_+$", "", re.sub(r"[^a-z0-9]+", "_", trim(s).lower()))


def collapse_ws(s: str) -> str:
    return " ".join(p for p in re.split(r"\s+", s) if p)


def ms(x: str) -> int:
    """Epoch ms of a canonical date ("2026-10-19") or datetime ("2026-10-19T08:00:00.000Z")."""
    y, mo, d = int(x[0:4]), int(x[5:7]), int(x[8:10])
    if len(x) == 10:
        return date_utc(y, mo - 1, d)
    h, mi, s = int(x[11:13]), int(x[14:16]), int(x[17:19])
    frac = int(x[20:23]) if len(x) > 20 and x[19] == "." else 0
    return date_utc(y, mo - 1, d, h, mi, s) + frac


def _civil(days: int) -> tuple[int, int, int]:
    z = days + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    return (y + (m <= 2), m, d)


def iso_datetime(t: int) -> str:
    days, rem = divmod(t, 86400000)
    y, m, d = _civil(days)
    h, rem = divmod(rem, 3600000)
    mi, rem = divmod(rem, 60000)
    s, ms_ = divmod(rem, 1000)
    return f"{y:04d}-{m:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}.{ms_:03d}Z"


def iso_date(t: int) -> str:
    return iso_datetime(t)[:10]


def hours_between(a, b):
    if a is None or b is None:
        return None
    return D.div(str(ms(b) - ms(a)), "3600000")


def days_between(a, b):
    if a is None or b is None:
        return None
    return str((ms(b[:10]) - ms(a[:10])) // 86400000)


def week_of(d):
    if d is None:
        return None
    t = ms(d[:10])
    days = t // 86400000
    dow = (days + 3) % 7  # 1970-01-01 was a Thursday; Monday = 0
    return iso_date(t - dow * 86400000)


def bucket(x, edges, labels):
    if x is None:
        return None
    for i, e in enumerate(edges):
        if e is not None and D.cmp(x, e) <= 0:
            return labels[i] if i < len(labels) else None
    return labels[len(edges)] if len(labels) > len(edges) else None


def fmt_num(x):
    return None if x is None else D.round_(x, 1)


def fmt_pct(x):
    return None if x is None else f"{D.round_(x, 1)}%"


def fmt_hours(h):
    if h is None:
        return None
    if D.cmp(h, "48") < 0:
        return f"{D.round_(h, 1)} h"
    return f"{D.fixed(D.div(h, '24', 1), 1)} d"


ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK"}


def fmt_money(m, cur):
    if m is None:
        return None
    c = (cur or "").upper()
    places = 0 if c in ZERO_DECIMAL else 2
    v = D.fixed(D.div(D.abs_(m), "100" if places else "1", places), places)
    i, _, f = v.partition(".")
    neg = D.cmp(m, "0") < 0
    i = re.sub(r"\B(?=(\d{3})+(?!\d))", ",", i)
    return f"{'-' if neg else ''}{i}{'.' + f if f else ''}{' ' + c if c else ''}"


def first_sentence(s: str) -> str:
    m = re.match(r"[\s\S]*?[.!?](?=\s|$)", s)
    return trim(m.group(0) if m else s)


def fmt_date(d):
    return None if d is None else d[:10]


def cmp_text(a: str, b: str) -> int:
    return -1 if a < b else 1 if a > b else 0
