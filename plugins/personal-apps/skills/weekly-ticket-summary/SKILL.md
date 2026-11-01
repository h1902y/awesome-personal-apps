---
name: weekly-ticket-summary
description: Turns last week's support ticket export (CSV or pasted rows) into a short weekly summary a support team can read in two minutes, with tickets grouped by theme and counted, one real ticket quoted per theme, and urgent or overdue tickets listed first with how long they have waited. Use when someone shares a help desk export and asks for a weekly ticket summary, review, digest or triage.
metadata:
  version: 1.2.7
compatibility: Runs a small Python script with uv (or python3). Reads only the files you give it; no network.
---

# Weekly ticket summary

You turn one week of support tickets into a summary the team actually reads. A person still decides what to do; your job is to make sure nothing urgent gets missed and the patterns are easy to see.

**You never count.** The app (`app.py`) reads the data, counts, sorts, checks every number and builds the output. You only do the steps that need judgement (the AI steps below), in the exact shape it asks for, and then print what it builds.

## Input

- A CSV export (or rows pasted into the chat: save them as a `.csv` file first).
- No file yet? Ask for one, and offer to try it on the sample first (`sample-tickets.csv`): `app.py sample`.

## Available scripts

- `scripts/app.py` in this skill's folder: the whole app. Stdlib Python; it keeps its data in `~/.h1902y/weekly-ticket-summary/` (or `$CLAUDE_PLUGIN_DATA`).
  - Claude Code: `uv run --managed-python "${CLAUDE_SKILL_DIR}/scripts/app.py" <command>`
  - Elsewhere, use the path of this skill's folder in place of `${CLAUDE_SKILL_DIR}`. Without `uv`, run the same file with `python3` (Windows: `py`).
  - Run it as one plain command with the full path: no `cd`, no shell variables, so the user sees exactly what runs.

## What to do

1. **Load a week of tickets.** `app.py run load FILE` (or `app.py sample`). It says how many rows it read and skipped and which columns it couldn't find. If a column was guessed wrong, `app.py map <table>.<field>=<column>` and run it again.
2. **Name themes and write the summary.** `app.py run read_week`. It stops at each AI step and prints a task file:
   - `flag` (classify): label only the rows that match one of the labels; for each give `key` (the row's key), `label` and a few words of `reason`. Flag only what the customer wrote; don't guess intent. Reason: a few words from the ticket itself.
   - `themes` (group): put every row in exactly one group; for each row give `group` (a short plain name) and `key`. Put every ticket in exactly one theme by what it's about. If the category field already groups them well, use the category names.
   - `quotes` (pick_quote): for each group, pick one row and copy a short phrase from its text word for word: `group`, `key`, `quote`. Pick the short phrase, word for word from one ticket's message, that best shows the theme's pattern.
   - `summary` (summarize): write the `text` in the sentence range asked; use only numbers that appear in the inputs. Three to five sentences a team lead could read out at the Monday stand-up: the biggest theme, what stands out, what needs a reply today. Use only numbers from the inputs.
   For each one: read the task file (instructions, rules, inputs, output `shape`), write the output as a JSON list of rows, then `app.py submit <run> <step> OUTPUT.json`. If it prints problems, fix exactly those and submit once more; after a second miss the app uses its own version.
3. **Show it.** `app.py show report`, and print the markdown exactly as it is.

When the user asks for more:
- **Draft replies for urgent:** `app.py run draft_replies`, then the same AI steps loop, then `app.py show summary`.
- **Write escalation note:** `app.py run escalation_note`, then the same AI steps loop, then `app.py show summary`.
- **Mark one done or snoozed:** `app.py mark <id> done` (or `snoozed --for 1d`, or `open`).

## App agents

This app has 4 app agents, each with its own generated instructions and tools; `app.py` checks every call an agent makes against its own list (`--agent <name>`) and refuses the rest.

| Agent | Role | What for |
|---|---|---|
| `analyst` (start here): Ask about this week | analyst | Answer the support lead's questions about this week's tickets and send the work to the right agent. |
| `triage`: Triage | triage | Flag angry, cancelling and repeat tickets, group the week into themes, and propose done or snooze on the urgent queue. |
| `drafter`: Draft replies | drafter | Draft short replies to the open urgent tickets and pick one real quote per theme. |
| `reporter`: Write it up | reporter | Write the stand-up summary and the escalation note for my manager from this week's numbers. |

- **Start with `analyst`** for questions about the data: in Claude Code, the `weekly-ticket-summary-analyst` subagent; elsewhere, follow `skills/weekly-ticket-summary-analyst/SKILL.md` in this context. Give it the user's request and the full path of this skill's `scripts/app.py`.
- **Handoffs route through this skill.** When an agent's reply ends with a handoff note (`from`, `to`, `period`, `keys`, `note`, `hops`), start the agent it names (`weekly-ticket-summary-<to>`) with that note and the full path of `scripts/app.py` as its first message. Subagents can't start subagents, so they always come back here. After 4 handoffs in one conversation, stop and answer the user yourself.
- **Approvals:** what an agent proposes, and anything a run it started would save, waits in `app.py show approvals`; run `app.py approve <n>` or `reject <n>` only after the user says which.

When the user asks:
- To **name themes and write the summary** (Weekly summary): start `weekly-ticket-summary-triage` with "Name the themes and flag the tickets for the {period}, then hand the quotes to the drafter and the summary to the reporter." (`{period}`: the period `app.py status` shows).
- To **write escalation note** (Weekly summary): start `weekly-ticket-summary-reporter` with "Write the escalation note for my manager for the {period}." (`{period}`: the period `app.py status` shows).
- To ask about one row of **urgent_queue1** (Urgent queue): start `weekly-ticket-summary-drafter` with the note `{period, keys: [<the row's id>]}`.
- To **draft replies for urgent** (Urgent queue): start `weekly-ticket-summary-drafter` with "Draft replies for the open urgent tickets in the {period}." (`{period}`: the period `app.py status` shows).

## Output

Whatever `app.py show` prints. The main output is `app.py show report`.

## Rules

- **Never count, sum, sort or work out a date yourself.** Every number comes from `app.py`. If you need one it doesn't print, say so.
- Quotes and evidence are copied word for word from the data; never paraphrase inside quotation marks.
- Drafts only: never send, post or promise anything on the user's behalf.
- Print what `app.py show` gives exactly as it is: don't change, add or recount any number, row or quote, and add no advice or sign-off unless asked.
- Don't guess customer intent beyond what they wrote.
- Customer data stays in this chat: don't suggest pasting it anywhere else.
