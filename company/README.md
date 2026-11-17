# Company workspace

Shared state for the agent team. Agents don't remember earlier runs. They read and write these files.

| File | Owner | What it holds | In git? |
|---|---|---|---|
| `icp/pet.md`, `icp/hair.md`, `icp/nails.md` | You | Who we sell to in each industry and how to qualify them | Yes |
| `leads.csv` | prospector | Qualified businesses, one per row | **No**, private |
| `outreach/` | sales-writer | Notes on which drafts were written for which lead | **No**, private |
| `ops-log.md` | sre | Daily health, cost and incident notes | **No**, private |

Private files are listed in the repo's `.gitignore`. Copy `leads.example.csv` to `leads.csv` to start.

## Agents

Defined in `.claude/agents/`:

- **prospector**: finds and qualifies businesses into `leads.csv`. Never contacts anyone.
- **sales-writer**: writes personalized outreach as **Gmail drafts** only. You review and send.
- **sre**: daily read-only health and cost check, written to `ops-log.md`.

## Ground rules

1. Agents draft; you send, post, merge and pay.
2. Only public business information goes in `leads.csv`: no personal phone numbers or home addresses.
3. Anyone who says no or asks not to be contacted gets `status=do_not_contact` and is never drafted to again.

## Lead statuses

`new` → `qualified` → `drafted` → `contacted` → `replied` → `demo` → `pilot` → `customer`, or `not_a_fit` / `do_not_contact`.

## Running them

In Claude Code, from the repo root:

```
Use the prospector agent to find 20 pet groomers in <city>.
Use the sales-writer agent to draft emails for qualified pet leads.
Use the sre agent to run today's health check.
```

To run them on a schedule, use `/schedule` (cloud) or a scheduled task.
