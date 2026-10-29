# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Weekly ticket summary: the exact numbers, so the model never counts.

Two steps, both read only the file you give them and nothing else (no network):

    summarize.py TICKETS.csv                      facts as JSON: column mapping, rows read and skipped, missing
                                                  columns, every ticket, the rule-based urgent list, category counts
    summarize.py TICKETS.csv --notes NOTES.json   the finished summary in markdown, built from the facts plus the
                                                  model's notes; prints {"errors": [...]} and exits 1 if a note is wrong

NOTES.json (use `--notes -` to read it from stdin):

    {
      "flags":    {"TB-1": "third time asking; threatening to cancel"},   # angry, cancelling or repeat; may be {}
      "problems": {"TB-1": "Refund still missing"},                      # a few words per urgent ticket (optional)
      "themes":   {"Billing and refunds": ["TB-1", "TB-2"]},              # every ticket in exactly one theme,
                                                                         # or the string "category" to use that column
      "quotes":   {"Billing and refunds": {"id": "TB-1", "text": "Refund promised two weeks ago, nothing."}},
      "summary":  "Three to five sentences."
    }

Options: `--map field=column` fixes a guessed column (fields: id customer subject message category status priority
waiting created target due); `--now 2026-10-26T09:00` sets the clock when waiting time comes from a date column.

Rules (keep in step with SKILL.md and the Cloudflare OS brief):
- urgent = priority High/Urgent (or Critical, P0, P1), or waiting longer than the target; flagged tickets join too;
- sorted by hours over target (largest first; no target last), then by waiting time;
- waiting shows `<n> h` under 48 hours, else `<n> d` with one decimal; numbers round half up;
- share = theme tickets / rows read, one decimal at most.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

FIELDS = {
    # field: header names that mean it, best first (headers are lowercased, non-alphanumerics become _)
    "id": ["ticket_id", "ticket_number", "ticket_no", "ticket", "id", "case_id", "case_number", "conversation_id",
           "issue_id", "number", "key"],
    "customer": ["customer", "customer_name", "company", "company_name", "organization", "organisation", "account",
                 "account_name", "client", "requester", "requester_name", "contact", "contact_name", "from", "email",
                 "requester_email", "customer_email"],
    "subject": ["subject", "title", "summary", "topic"],
    "message": ["message", "description", "body", "first_message", "initial_message", "content", "text", "comment",
                "details", "question"],
    "category": ["category", "type", "ticket_type", "issue_type", "tags", "tag", "labels", "group", "queue", "topic"],
    "status": ["status", "state"],
    "priority": ["priority", "urgency", "severity"],
    "waiting": ["hours_open", "hours_waiting", "waiting_hours", "wait_hours", "open_hours", "age_hours",
                "hours_since_created", "hours_since_last_reply", "waiting_time", "wait_time", "time_open", "age",
                "hours"],
    "created": ["created", "created_at", "created_date", "date_created", "opened", "opened_at", "submitted",
                "submitted_at", "received", "last_customer_reply", "last_reply", "last_reply_at", "date"],
    "target": ["sla_hours", "target_hours", "sla", "sla_target", "target", "response_target", "sla_target_hours",
               "first_response_target", "due_hours"],
    "due": ["due", "due_at", "due_date", "due_by", "sla_due", "sla_due_at", "sla_breach_at", "breach_at"],
}
# The columns the summary needs; waiting and target can each come from either of two kinds of column.
NEEDED = ["id", "customer", "subject", "message", "category", "status", "priority", "waiting time", "target"]
URGENT_PRIORITIES = {"high", "urgent", "critical", "highest", "emergency", "p0", "p1", "sev1", "sev_1", "blocker"}


def norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


def half_up(x: float, places: int = 1) -> Decimal:
    return Decimal(str(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def num(x: float | Decimal) -> str:
    """At most one decimal, no trailing .0: 166, 17.5."""
    d = half_up(float(x))
    return str(int(d)) if d == d.to_integral_value() else str(d)


def fmt_wait(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours < 48:
        return f"{num(hours)} h"
    return f"{half_up(hours / 24)} d"


def parse_hours(value: str) -> float | None:
    """'30', '30.5', '30 h', '2 days', '1,5' -> hours."""
    v = value.strip().lower().replace(",", ".")
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|d|day|days|m|min|mins|minutes)?", v)
    if not m:
        return None
    n, unit = float(m.group(1)), (m.group(2) or "h")
    return n * 24 if unit.startswith("d") else n / 60 if unit.startswith("m") else n


DATE_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%m/%d/%Y %H:%M",
                "%m/%d/%Y", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%b %d, %Y %H:%M", "%b %d, %Y", "%d %b %Y %H:%M", "%d %b %Y"]


def parse_date(value: str) -> datetime | None:
    v = value.strip()
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        d = None
        for f in DATE_FORMATS:
            try:
                d = datetime.strptime(v, f)
                break
            except ValueError:
                continue
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def map_columns(headers: list[str], overrides: dict[str, str]) -> dict[str, str]:
    """field -> original header. Exact names first, then a header that contains the name; each header used once."""
    by_norm = {norm(h): h for h in headers}
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for field, col in overrides.items():
        hit = col if col in headers else by_norm.get(norm(col))
        if hit is None:
            sys.exit(f"--map {field}={col}: no such column (columns: {', '.join(headers)})")
        mapping[field] = hit
        used.add(hit)
    for field, names in FIELDS.items():
        if field in mapping:
            continue
        hit = next((by_norm[n] for n in names if n in by_norm and by_norm[n] not in used), None)
        if hit is None:
            hit = next((h for n in names if len(n) > 3 for h in headers
                        if h not in used and n in norm(h)), None)
        if hit is not None:
            mapping[field] = hit
            used.add(hit)
    return mapping


def read_rows(path: Path) -> tuple[list[str], list[list[str]], int]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return [], [], 0
    headers = [h.strip() for h in rows[0]]
    good, skipped = [], 0
    for r in rows[1:]:
        while len(r) > len(headers) and not r[-1].strip():
            r = r[:-1]  # trailing empty cells some exports add
        if len(r) != len(headers):
            skipped += 1
            continue
        good.append([c.strip() for c in r])
    return headers, good, skipped


def facts(path: Path, overrides: dict[str, str], now: datetime) -> dict:
    headers, rows, skipped = read_rows(path)
    if not headers:
        return {"rows_read": 0, "rows_skipped": 0, "error": "the file has no rows"}
    mapping = map_columns(headers, overrides)
    col = {f: headers.index(h) for f, h in mapping.items()}
    tickets = []
    for r in rows:
        get = lambda f: r[col[f]] if f in col else ""
        tid = get("id")
        if not tid and not get("message") and not get("subject"):
            skipped += 1
            continue
        waiting = parse_hours(get("waiting")) if "waiting" in col else None
        if waiting is None and "created" in col and (d := parse_date(get("created"))):
            waiting = (now - d).total_seconds() / 3600
        target = parse_hours(get("target")) if "target" in col else None
        over = waiting - target if waiting is not None and target is not None else None
        if over is None and "due" in col and (d := parse_date(get("due"))):
            over = (now - d).total_seconds() / 3600
        tickets.append({"id": tid or f"row {len(tickets) + 2}", "customer": get("customer"), "subject": get("subject"),
                        "message": get("message"), "category": get("category"), "status": get("status"),
                        "priority": get("priority"), "waiting_hours": waiting, "target_hours": target,
                        "over_target_hours": over, "waiting": fmt_wait(waiting)})
    either = {"waiting time": ("waiting", "created"), "target": ("target", "due")}
    missing = [n for n in NEEDED if not any(k in col for k in either.get(n, (n,)))]
    categories: dict[str, int] = {}
    for t in tickets:
        if t["category"]:
            categories[t["category"]] = categories.get(t["category"], 0) + 1
    return {"rows_read": len(tickets), "rows_skipped": skipped, "columns": mapping, "columns_missing": missing,
            "urgent_by_rule": [urgent_row(t, "") for t in sort_urgent([t for t in tickets if rule_urgent(t)])],
            "category_counts": dict(sorted(categories.items(), key=lambda kv: -kv[1])),
            "tickets": tickets}


def rule_urgent(t: dict) -> bool:
    return norm(t["priority"]) in URGENT_PRIORITIES or (t["over_target_hours"] or 0) > 0


def sort_urgent(ts: list[dict]) -> list[dict]:
    return sorted(ts, key=lambda t: (t["over_target_hours"] is None, -(t["over_target_hours"] or 0),
                                     -(t["waiting_hours"] or 0)))


def urgent_row(t: dict, flag: str, problem: str = "") -> dict:
    why = []
    if norm(t["priority"]) in URGENT_PRIORITIES:
        why.append(f"{t['priority']} priority")
    over = t["over_target_hours"]
    if over is not None and over > 0:
        why.append(f"{num(over)} h over its {num(t['target_hours'])} h target" if t["target_hours"] is not None
                   else f"{num(over)} h past due")
    if flag:
        why.append(flag)
    return {"id": t["id"], "customer": t["customer"], "problem": problem or t["subject"], "waiting": t["waiting"],
            "why": "; ".join(why)}


def cell(s: str) -> str:
    return " ".join(str(s).split()).replace("|", "\\|")


def report(f: dict, notes: dict) -> tuple[str, list[str]]:
    errs: list[str] = []
    tickets = f["tickets"]
    by_id: dict[str, list[dict]] = {}
    for t in tickets:
        by_id.setdefault(t["id"], []).append(t)
    flags = notes.get("flags") or {}
    problems = notes.get("problems") or {}
    for tid in list(flags) + list(problems):
        if tid not in by_id:
            errs.append(f"flags/problems: no ticket {tid} in the file")

    # themes
    themes = notes.get("themes")
    if themes == "category":
        themes = {}
        for t in tickets:
            themes.setdefault(t["category"] or "Uncategorised", []).append(t["id"])
    if not isinstance(themes, dict) or not themes:
        errs.append('themes: give {"Theme name": ["id", ...]} or "category"')
        themes = {}
    seen: dict[str, str] = {}
    for name, ids in themes.items():
        for tid in ids:
            if tid not in by_id:
                errs.append(f"themes: {name}: no ticket {tid} in the file")
            elif tid in seen and seen[tid] != name:
                errs.append(f"themes: {tid} is in both {seen[tid]} and {name}")
            seen[tid] = name
    unassigned = [tid for tid in by_id if tid not in seen]
    if unassigned:
        errs.append(f"themes: {len(unassigned)} ticket(s) in no theme: {', '.join(unassigned)}")

    # quotes, word for word
    quotes = notes.get("quotes") or {}
    for name in themes:
        q = quotes.get(name)
        if not q or not q.get("id") or not q.get("text"):
            errs.append(f"quotes: {name}: needs {{\"id\": ..., \"text\": ...}}")
            continue
        if q["id"] not in themes[name]:
            errs.append(f"quotes: {name}: {q['id']} isn't in this theme")
        elif not any(q["text"].strip() in t["message"] for t in by_id.get(q["id"], [])):
            errs.append(f"quotes: {name}: the text isn't word for word in {q['id']}'s message; copy it exactly from "
                        f"\"{by_id[q['id']][0]['message']}\"")
    for name in quotes:
        if name not in themes:
            errs.append(f"quotes: {name} isn't a theme")
    summary = (notes.get("summary") or "").strip()
    if not summary:
        errs.append("summary: missing")
    if errs:
        return "", errs

    urgent = sort_urgent([t for t in tickets if rule_urgent(t) or t["id"] in flags])
    n = f["rows_read"]
    counts = sorted(((name, sum(len(by_id[i]) for i in ids)) for name, ids in themes.items()), key=lambda x: -x[1])
    out = [f"# Weekly ticket summary: {n} tickets", "", f"## Reply to these first ({len(urgent)})",
           "| Ticket | Customer | Problem | Waiting | Why |", "|---|---|---|---|---|"]
    for t in urgent:
        u = urgent_row(t, flags.get(t["id"], ""), problems.get(t["id"], ""))
        out.append(f"| {cell(u['id'])} | {cell(u['customer'])} | {cell(u['problem'])} | {u['waiting']} | {cell(u['why'])} |")
    out += ["", "## Themes", "| Theme | Tickets | Share | Example |", "|---|---|---|---|"]
    for name, c in counts:
        q = quotes[name]
        out.append(f"| {cell(name)} | {c} | {num(c * 100 / n)}% | \"{cell(q['text'].strip())}\" ({cell(q['id'])}) |")
    missing = f"columns missing: {', '.join(f['columns_missing'])}" if f["columns_missing"] else "no columns missing"
    out += ["", "## Summary", summary, "",
            f"{n} rows read, {f['rows_skipped']} skipped, {missing}."]
    return "\n".join(out) + "\n", []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--notes", help="the model's notes as JSON (a file, or - for stdin)")
    ap.add_argument("--map", action="append", default=[], metavar="FIELD=COLUMN")
    ap.add_argument("--now", help="ISO date-time to measure waiting time from (default: now)")
    a = ap.parse_args()
    overrides = dict(m.split("=", 1) for m in a.map)
    now = parse_date(a.now) if a.now else datetime.now(timezone.utc)
    f = facts(a.csv, overrides, now)
    if not a.notes:
        print(json.dumps(f, indent=1, ensure_ascii=False))
        return
    notes = json.load(sys.stdin if a.notes == "-" else open(a.notes, encoding="utf-8"))
    md, errs = report(f, notes)
    if errs:
        print(json.dumps({"errors": errs}, indent=1, ensure_ascii=False))
        sys.exit(1)
    sys.stdout.write(md)


if __name__ == "__main__":
    main()
