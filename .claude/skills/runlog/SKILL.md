---
name: runlog
description: Append an audit entry to the repo's run log (runlog/YYYY-MM-DD-name.md) recording the user's prompt verbatim, what was done, the justification, and verification. Use after completing any task in this repo that changes files, git state, the database or the environment, and whenever the user says "log this", "update the run log" or "runlog".
---

# Run log

This repo keeps an auditable record of all work in `runlog/`. Format and rules are in `runlog/README.md`; follow them exactly.

## When

- After finishing any task that changed files, commits, branches, the database or the environment. One entry per user prompt; if a prompt led to several distinct changes, one entry still, with each change listed under **Done**.
- Read-only questions (explanations, lookups) do not need an entry unless the user asks.
- Write the entry before the commit that contains the work, so the run log ships in the same commit or pull request.

## How

1. Work out the file: `runlog/<today YYYY-MM-DD>-<name>.md`, where `<name>` is the git user's first name or handle in lowercase (ask once if unclear, then reuse). Create it with a `# Run log: YYYY-MM-DD, <name>` heading if it doesn't exist.
2. Append one entry at the bottom, in the format from `runlog/README.md`:
   - **Prompt:** the user's message quoted word for word as a blockquote, typos included. If they attached a file, note it in brackets, e.g. `[attached: PRD (1).pdf]`. Never paraphrase.
   - **Done:** what changed, concrete and short.
   - **Justification:** why this approach; each real choice, the options and who decided (user's answer, PRD, course notes, or your recommendation they accepted).
   - **Verification:** the commands run and their actual results. If something was not verified, failed, or was blocked (for example a denied permission), say so.
   - **Files**, **Commit** (short SHA or "not committed"), **Tool** (`Claude Code (<model id>)`).
3. Never include secrets, `.env` contents or tokens; write `[redacted]`.
4. Do not edit or delete earlier entries. To correct one, add a new entry that says what was wrong.
5. Tell the user in one line that the run log was updated, with the file path.
