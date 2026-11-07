# Run log

An audit trail of the work on this repo: what was asked, what was done, why, and how it was checked. Anyone should be able to read an entry and understand a change without asking the person who made it.

## Files

One file per person per day: `runlog/YYYY-MM-DD-name.md`, e.g. `2026-11-07-vidhi.md`. Separate files keep teammates from hitting merge conflicts. Add new entries to the bottom of your file for the day.

## Entry format

```markdown
## HH:MM · Short title

**Prompt:**
> The request exactly as given, word for word. For a typed prompt to an AI tool, paste it.
> For your own work, write the task as you'd have asked for it.

**Done:**
- What changed, in plain terms.

**Justification:**
- Why this approach, and any choice made between options (what was picked, what was rejected, why).
- Who decided: you, a teammate, the PRD, or the tool's recommendation you accepted.

**Verification:** what was run and what it showed (e.g. "make test: 14 passed"). Say plainly if something was not checked or failed.

**Files:** paths changed
**Commit:** short SHA, or "not committed"
**Tool:** e.g. "Claude Code (claude-opus-5-5)" or "manual"
```

## Rules

- Quote prompts verbatim, typos included. Paraphrasing defeats the audit.
- Never paste secrets: API keys, `.env` contents, tokens. Write `[redacted]`.
- Record failures and reversals too (e.g. a change that was undone), not just the successes.
- The run log goes in the same pull request as the work it describes.
