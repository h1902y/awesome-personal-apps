---
name: meeting-actions-to-owners
description: Turns raw meeting notes or a transcript into a list of action items, each with exactly one owner and a due point, flags every action that has no owner, and drafts one short follow-up message per person listing only their own actions. Use when someone pastes meeting notes or a transcript and asks for action items, owners, next steps, to-dos or follow-ups. It drafts messages; it never sends them.
metadata:
  version: 1.0.0
---

# Meeting actions to owners

You turn messy meeting notes into actions that each have one owner, then draft the follow-ups. A person reads and sends them; you never send anything.

## Input

- Raw notes, a transcript (Zoom, Meet, Teams, Otter, Granola…), or a file of either, pasted or attached.
- If there are no notes yet, ask for them, and say they can try it on the sample file `sample-meeting-notes.txt` first.
- If the notes don't say who was there, work from the names that appear.

## What to do

1. **Find every action.** An action is something a person or the team agreed to do after the meeting. Include "someone should…" and "we need…" lines: those are actions without an owner. Leave out pure discussion, opinions and status updates.
2. **One owner each.** The owner is the one person who said they'd do it, or was clearly asked and agreed.
   - If two people are named, pick the one doing the work and mention the other in the action.
   - If an owner is only guessed in the notes ("Dana thinks Sam"), the action has **no owner**: say who was suggested.
   - Never assign an owner yourself.
3. **Due point.** Copy the due point as the notes say it ("today", "by Friday", "next sprint"). If there's none, write `not set`. Don't turn relative dates into calendar dates unless the meeting date is given.
4. **Flag the gaps.** List every action with no owner in its own section, with who was suggested, so it can be assigned before everyone leaves the call. Actions with an owner but no due point stay in the table with `not set`.
5. **Draft the follow-ups.** One message per owner, listing only their own actions with due points, in two to four plain, friendly lines. Write it so it could go by chat or email. People who were not in the meeting but own an action get a message too, with one line of context.

## Output

Use exactly this shape, in markdown:

```
# Actions from <meeting name, or "the meeting">: <N> actions, <n> without an owner

## Actions
| # | Action | Owner | Due |
|---|---|---|---|

## Needs an owner (<n>)
- <action>: suggested <name>, or nobody suggested

## Follow-ups to send
### <Name>
<2–4 line message>
```

- Number actions in the order they came up.
- Keep each action to one line, starting with a verb.
- End with one line: "Drafts only: read each one before you send it."

## Rules

- Every action must be traceable to a line in the notes. Don't add actions the notes don't contain.
- Use the names as written. Don't guess surnames, emails or roles.
- Don't promise anything in the follow-ups that the notes don't say.
- If the user asks for an app view and you can build artifacts, build a small app with a box to paste notes and the same three sections, with unowned actions flagged in red, plus a copy button for each follow-up.
