---
name: weekly-ticket-summary
description: Turns last week's support ticket export (CSV or pasted rows) into a short weekly summary a support team can read in two minutes, with tickets grouped by theme and counted, one real ticket quoted per theme, and urgent or overdue tickets listed first with how long they have waited. Use when someone shares a help desk export and asks for a weekly ticket summary, review, digest or triage.
compatibility: Runs a small Python script with uv (or python3). Reads only the file you give it; no network.
metadata:
  version: 1.1.0
---

# Weekly ticket summary

You turn one week of support tickets into a summary the team actually reads. A person still decides what to do; your job is to make sure nothing urgent gets missed and the patterns are easy to see.

**You never count.** A script counts rows, finds urgent and overdue tickets, works out waiting times, counts themes and checks quotes. You only judge what needs reading: who is angry, how tickets group, which quote to show, and the summary. Then you print the script's output as it is.

## Input

- A CSV export from any help desk (Zendesk, Freshdesk, Help Scout, Intercom, a spreadsheet), or rows pasted into the chat. Save pasted rows as a `.csv` file first.
- If there's no file yet, ask for one, and say they can try it on the sample file `sample-tickets.csv` first.

## Available scripts

- `scripts/summarize.py` in this skill's folder. Stdlib Python, reads only the file you pass.
  - Claude Code: `uv run --managed-python "${CLAUDE_SKILL_DIR}/scripts/summarize.py" FILE.csv`
  - Elsewhere, use the path of this skill's folder in place of `${CLAUDE_SKILL_DIR}`.
  - If `uv` isn't installed, run the same file with `python3` (Windows: `py`).
  - Run it as one plain command with the full path: no `cd`, no shell variables, so the user sees exactly what runs. It asks for approval the first time.

## What to do

1. **Get the facts.** Run `summarize.py FILE.csv`. It prints JSON:
   - `columns`: how it mapped the columns by meaning. If one is wrong, run it again with `--map field=column` (fields: id, customer, subject, message, category, status, priority, waiting, created, target, due).
   - `rows_read`, `rows_skipped`, `columns_missing`;
   - `tickets`: every readable row, with `waiting` already formatted;
   - `urgent_by_rule`: tickets with priority High/Urgent or waiting longer than their target, already sorted;
   - `category_counts`: tickets per value of the category column.
2. **Flag by reading.** Read every ticket's subject and message. Flag a ticket if the customer is angry, threatening to cancel, or asking for the second or third time. Write a few words on why (e.g. `third time asking; threatening to cancel`). Flag only what they wrote; don't guess intent.
3. **Group by theme.** Put every ticket in exactly one theme. If the category column makes sense, use `"category"` and the script groups by it. Otherwise name 4–8 themes in plain words and list the ticket ids in each. Don't count them; the script does.
4. **Pick one quote per theme.** Copy a short phrase word for word from one ticket's `message` in that theme, the one that best shows the pattern. Never paraphrase.
5. **Write the summary.** Three to five sentences a team lead could read out in a stand-up: the biggest theme, what stands out, and what needs a reply today. Any number in it must come from the script's output (counts, waiting times, how many urgent).
6. **Build the report.** Write your notes as JSON and run `summarize.py FILE.csv --notes NOTES.json` (or `--notes -` and pipe the JSON in):

   ```json
   {
     "flags":    {"TB-4212": "third time asking; threatening to cancel"},
     "problems": {"TB-4212": "Promised refund still missing"},
     "themes":   {"Billing and refunds": ["TB-4210", "TB-4212"]},
     "quotes":   {"Billing and refunds": {"id": "TB-4212", "text": "Refund promised two weeks ago, nothing."}},
     "summary":  "Three to five sentences."
   }
   ```

   - `problems`: a few words on each urgent ticket's problem (optional; the subject is used otherwise).
   - If it prints `{"errors": [...]}`, fix your notes (a ticket in no theme, a quote that isn't word for word) and run it again.
7. **Print the markdown it outputs, exactly as it is.** Don't change, add or recount any number, row or quote. Don't add advice, next steps or a sign-off unless asked.

## Output

The script prints this shape:

```
# Weekly ticket summary: <N> tickets

## Reply to these first (<n>)
| Ticket | Customer | Problem | Waiting | Why |
|---|---|---|---|---|

## Themes
| Theme | Tickets | Share | Example |
|---|---|---|---|

## Summary
<3–5 sentences>

<one line: rows read, rows skipped, columns missing>
```

## No Python here (Cloudflare OS, or code that runs in a browser)

If you can't run the script, compute the numbers in code (JavaScript in a gadget, or any code tool you have), never in your head, with these exact rules:

- **Rows:** parse the CSV with a real parser (quoted fields can hold commas). Rows read = rows with the header's number of fields; the rest are skipped. Columns are mapped by meaning: id, customer, subject, message, category or tags, status, priority, waiting time (hours open, or created date), target (SLA hours, or due date).
- **Urgent:** priority is High, Urgent, Critical, P0 or P1, or waiting hours > target hours (strictly greater), or you flagged it (angry, cancelling, repeat). Sort by hours over target, largest first (no target last), then by waiting hours, largest first.
- **Waiting:** under 48 hours `<n> h`; otherwise `<n> d` = hours / 24 with one decimal, rounding half up (`Math.round(h / 24 * 10) / 10`, shown with `.toFixed(1)`).
- **Why:** `<priority> priority` if urgent by priority; `<over> h over its <target> h target` if over; then your flag. Joined with `; `.
- **Themes:** count the ticket ids you assigned to each; check every ticket is in exactly one theme and the counts add up to rows read. Share = count / rows read × 100, one decimal at most (`17.5%`, `20%`).
- **Quotes:** check in code that each quote is a substring of its ticket's message (`message.includes(quote)`); if not, pick another.
- Run the code, then print its numbers as they come out.

## Rules

- Every number comes from the script (or, without Python, from code). Never count or round in your head.
- Every quote must be word for word from the file; the script checks it.
- Don't guess customer intent beyond what they wrote.
- Customer data stays in this chat: don't suggest pasting it anywhere else.
- If the user asks for an app view and you can build artifacts, build a small app that takes the CSV and shows the same three sections, computing the numbers in its code with the rules above, so they can drop next week's file into it.
