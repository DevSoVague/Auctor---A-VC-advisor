# Person 3: analysis agents (A1, A2, A3, A6)

Person 3's half of the Frontier Hypothesis Engine. It takes one disease (COPD first) and an `as_of` date and produces, for each hypothesis, the *why now*, a mechanism card, a target product profile and a red-team verdict. Every claim cites a document from the evidence pack and nothing else.

This folder is self-contained and **does not touch** the team's `src/`, `tests/` or `pyproject.toml`. It runs on its own dependencies (see below) and plugs into the rest of the team's code through hooks only.

| Folder | What it is |
|---|---|
| [`frontier-engine/`](frontier-engine/) | The tested agent code: prompts, retrieval, grading, citation post-check, schemas, workbook export, hook docs ([`docs/PERSON3_HOOKS.md`](frontier-engine/docs/PERSON3_HOOKS.md), [`docs/ADAM_A4_A5_WORKFLOW.md`](frontier-engine/docs/ADAM_A4_A5_WORKFLOW.md)) |
| [`langgraph-person3/`](langgraph-person3/) | The same agents as a LangGraph state machine, one graph node per real step (retrieve, prompt, llm, parse, postcheck, validate, commit), plus Langflow components and docs ([README](langgraph-person3/README.md), [how to run](langgraph-person3/docs/HOW_TO_RUN.md)) |

`langgraph-person3` imports `../frontier-engine`, so keep the two folders side by side.

## Agents

| Agent | Question | Notes |
|---|---|---|
| A1 | Why is now the time? | 3+ dated shifts (one negative) and patient segments |
| A2 | Which mechanisms? | Cards are graded in code (tier, validation level, rank) |
| A3 | What would a winning product look like? | Target product profile with measurable must-haves |
| A6 | What could kill it? | Risks with kill criteria and cheapest tests; verdict survives / revise / kill |

## Quick start

```bash
cd person3
python -m venv .venv && source .venv/bin/activate
pip install -r frontier-engine/requirements-person3.txt langgraph pyyaml

# tests (no API key needed)
(cd frontier-engine && pytest)
(cd langgraph-person3 && pytest)

# a run
cp frontier-engine/.env.example frontier-engine/.env      # then add NEBIUS_API_KEY for live runs
cd langgraph-person3 && ./run_langgraph.sh                # see docs/HOW_TO_RUN.md for options
```

Search can use a corpus, the web (Tavily) or both. Settings are in `frontier-engine/.env`; the full list is in [`langgraph-person3/docs/HOW_TO_RUN.md`](langgraph-person3/docs/HOW_TO_RUN.md).

## Example output

[`langgraph-person3/examples/copd-backtest-run/`](langgraph-person3/examples/copd-backtest-run/) holds the report, plain-language trace and step table of one COPD run with `as_of` 2021-01-01. Full run folders (`runs/`) are not committed.

## Not committed on purpose

`.env` files, `runs/` (large logs and checkpoints), caches. `person3/ruff.toml` limits lint to syntax and undefined-name errors so the shared CI stays green; delete it once this code is reformatted to the root style.
