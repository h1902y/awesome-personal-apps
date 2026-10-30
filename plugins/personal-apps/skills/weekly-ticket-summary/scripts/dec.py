"""osgen engine, exact decimals, in Python: the same algorithm as runtime/js/dec.ts on Python ints (not the decimal
module, so the two can't drift on context settings). Values are canonical decimal strings."""
from __future__ import annotations

import re

DIV_SCALE = 12
_RE = re.compile(r"(-)?([0-9]+)(?:\.([0-9]+))?")


def is_dec(x) -> bool:
    return isinstance(x, str) and _RE.fullmatch(x) is not None


def _big(x: str) -> tuple[int, int]:
    m = _RE.fullmatch(x)
    if not m:
        raise ValueError(f"not a decimal: {x}")
    frac = m.group(3) or ""
    c = int(m.group(2) + frac)
    return (-c if m.group(1) else c, len(frac))


def _show(c: int, s: int) -> str:
    while s > 0 and c % 10 == 0:
        c //= 10  # exact: c is a multiple of 10
        s -= 1
    neg = c < 0
    digits = str(-c if neg else c)
    if s > 0:
        digits = digits.rjust(s + 1, "0")
        digits = f"{digits[:-s]}.{digits[-s:]}"
    return f"-{digits}" if neg else digits


def _align(a, b):
    s = max(a[1], b[1])
    return a[0] * 10 ** (s - a[1]), b[0] * 10 ** (s - b[1]), s


def _shift_half_up(c: int, shift: int) -> int:
    if shift <= 0:
        return c * 10 ** (-shift)
    p = 10 ** shift
    neg = c < 0
    a = -c if neg else c
    q, r = divmod(a, p)
    if r * 2 >= p:
        q += 1
    return -q if neg else q


def from_(x):
    if x is None or x == "":
        return None
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return _show(x, 0)
    if isinstance(x, float):
        if x != x or x in (float("inf"), float("-inf")):
            return None
        if x == int(x) and abs(x) < 2 ** 53:
            return _show(int(x), 0)
        t = repr(x)
        return None if "e" in t.lower() else _show(*_big(t))
    t = x.strip()
    return _show(*_big(t)) if is_dec(t) else None


def add(a, b):
    x, y, s = _align(_big(a), _big(b))
    return _show(x + y, s)


def sub(a, b):
    x, y, s = _align(_big(a), _big(b))
    return _show(x - y, s)


def mul(a, b):
    x, y = _big(a), _big(b)
    return _show(x[0] * y[0], x[1] + y[1])


def div(a, b, scale: int = DIV_SCALE):
    x, y = _big(a), _big(b)
    if y[0] == 0:
        return None
    k = scale + 1 + y[1] - x[1]
    num = x[0] * 10 ** k if k >= 0 else x[0]
    den = y[0] if k >= 0 else y[0] * 10 ** (-k)
    neg = (num < 0) != (den < 0)
    q = abs(num) // abs(den)
    r = _shift_half_up(q, 1)
    return _show(-r if neg else r, scale)


def neg(a):
    c, s = _big(a)
    return _show(-c, s)


def abs_(a):
    c, s = _big(a)
    return _show(abs(c), s)


def cmp(a, b) -> int:
    x, y, _ = _align(_big(a), _big(b))
    return -1 if x < y else 1 if x > y else 0


def round_(a, places: int = 0):
    c, s = _big(a)
    r = _shift_half_up(c, s - places)
    return _show(r, places) if places >= 0 else _show(r * 10 ** (-places), 0)


def fixed(a, places: int) -> str:
    c, s = _big(round_(a, places))
    c = c * 10 ** (places - s)
    neg_ = c < 0
    digits = str(-c if neg_ else c)
    if places > 0:
        digits = digits.rjust(places + 1, "0")
        digits = f"{digits[:-places]}.{digits[-places:]}"
    return f"-{digits}" if neg_ else digits


def is_zero(a) -> bool:
    return _big(a)[0] == 0
