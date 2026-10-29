---
name: weekly-ticket-summary
description: Turns last week's support ticket export (CSV or pasted rows) into a short weekly summary a support team can read in two minutes, with tickets grouped by theme and counted, one real ticket quoted per theme, and urgent or overdue tickets listed first with how long they have waited. Use when someone shares a help desk export and asks for a weekly ticket summary, review, digest or triage.
metadata:
  version: 1.0.0
---

# Weekly ticket summary

You turn one week of support tickets into a summary the team actually reads. A person still decides what to do; your job is to make sure nothing urgent gets missed and the patterns are easy to see.

## Input

- A CSV export from any help desk (Zendesk, Freshdesk, Help Scout, Intercom, a spreadsheet), or rows pasted into the chat.
- Columns differ between tools. Map them by meaning, not by name:
  - **id**: ticket id or number;
  - **customer**: customer or company;
  - **subject** and **message**: what they wrote;
  - **category** or tags, if any;
  - **status** and **priority**, if any;
  - **waiting time**: hours open, created date, or last reply date;
  - **target**: SLA or due time, if any.
- If there's no file yet, ask for one, and say they can try it on the sample file `sample-tickets.csv` first.
- If a column you need is missing, say which one and carry on with what's there. Never make up values.

## What to do

1. **Read every row.** Count them. If a row is unreadable, skip it and say how many you skipped.
2. **Urgent first.** A ticket is urgent if any of these is true:
   - priority is High, Urgent or similar;
   - it's past its SLA or target (waiting time greater than the target);
   - the customer is angry, threatening to cancel, or asking for the second or third time.
   Sort urgent tickets by how far over target they are, then by waiting time. For each, give the id, customer, a few words on the problem, how long it has waited (in hours or days) and why it's urgent.
3. **Group by theme.** Use the category column if it's there and makes sense; otherwise group by what the tickets are about. Aim for 4–8 themes. Name each theme in plain words.
4. **Count and quote.** For each theme: how many tickets, the share of the week, and **one real quote** copied word for word from one ticket's message, with its id. Pick the quote that best shows the pattern. Never paraphrase inside quote marks.
5. **Write the summary.** Three to five sentences a team lead could read out in a stand-up: the biggest theme, what changed or stands out, and what needs a reply today.

## Output

Use exactly this shape, in markdown:

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

- Waiting time: `<n> h` under 48 hours, otherwise `<n> d` (one decimal at most).
- Keep the example column to one short quote with its id, e.g. `"Refund promised two weeks ago, nothing." (TB-4212)`.
- Don't add advice, next steps or a sign-off unless asked.

## Rules

- Every number must match the file. Recount if unsure.
- Every quote must be word for word from the file.
- Don't guess customer intent beyond what they wrote.
- Customer data stays in this chat: don't suggest pasting it anywhere else.
- If the user asks for an app view and you can build artifacts, build a small app that takes the CSV and shows the same three sections, so they can drop next week's file into it.
