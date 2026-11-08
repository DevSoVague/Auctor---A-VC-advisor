# Working in this repo

How to set up, where your code goes, and how changes reach `main`. The spec is [PRD.md](PRD.md).

## One-time setup (about 5 minutes)

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker Desktop and git. You do not need to install Python; uv fetches the pinned version (3.11).

```bash
git clone https://github.com/vidhi-ds-24/Ctrl-Alt-Cure.git
cd Ctrl-Alt-Cure
make setup             # installs the exact package versions in uv.lock
cp .env.example .env   # then fill in your API keys; .env is never committed
make up                # starts Postgres 16 with pgvector (Docker Desktop must be running)
make schema            # creates the tables (once, on a fresh database)
make test              # should end with "passed"
```

If `make test` passes, you're set up. `make api` then serves the API at http://localhost:8000/health.

The database is on port **5433**, not Postgres's usual 5432, so it can't clash with a Postgres already installed on your machine (e.g. Postgres.app). If you copied `.env` before this change, set `DATABASE_URL=postgresql://frontier:frontier@localhost:5433/frontier` in it and run `make up` again.

## Where your code goes

Everything lives in one Python package, `frontier`, under `src/frontier/`. The PRD lists folders at the top level (`core/`, `agents/`...); in this repo they sit inside the package, so you import them as `frontier.core`, `frontier.agents`, and so on.

| Person | Folder | Import as |
|---|---|---|
| 1: Data | `src/frontier/ingest/` (the PRD's `data/`), `src/frontier/store/`, `src/frontier/eval/`, `pitch/` | `frontier.ingest`, `frontier.store`, `frontier.eval` |
| 2: Platform and app | `src/frontier/core/`, `src/frontier/api/`, `src/frontier/agents/schemas.py`, `src/frontier/agents/citations/`, `app/` | `frontier.core`, `frontier.api`, `frontier.agents.schemas` |
| 3: Analysis | `src/frontier/agents/analysis/` | `frontier.agents.analysis` |
| 4: Competitor | `src/frontier/agents/competitor/` | `frontier.agents.competitor` |
| 5: Flow and memo | `src/frontier/flow/`, `src/frontier/memo/` | `frontier.flow`, `frontier.memo` |

Prompts go in `src/frontier/agents/prompts/`, one `.md` per agent. Tests go in `tests/`, fixtures in `tests/fixtures/`.

## Building an agent against the contracts

Everything you need to build and test an agent works today with no database, no API keys and no model calls.

- **What goes in and out:** `src/frontier/agents/schemas.py`. `AGENTS` maps each agent name to its (input, output) models, e.g. `a3_profile` takes `A3Input` and returns `ProductProfile`.
- **The shape of an agent:**

  ```python
  from frontier.agents.schemas import A3Input, ProductProfile
  from frontier.core.context import RunContext

  async def run(inp: A3Input, ctx: RunContext) -> ProductProfile:
      hits = await ctx.retrieval.search("PDE4 COPD exacerbations")  # already limited to ctx.config.as_of
      messages = [...]  # system prompt from src/frontier/agents/prompts/a3_profile.md, then inp and hits
      return await ctx.llm(agent="a3_profile", messages=messages, schema=ProductProfile)
  ```

- **Testing it:** `make_context(config)` builds a `ctx`. With `LLM_MODE=stub` (which `make test` sets), `ctx.llm` returns `tests/fixtures/a3_profile.output.json` instead of calling a model, and `ctx.retrieval` serves the made-up records in `tests/fixtures/retrieval/records.json`. Sample inputs: `load_fixture("a3_profile.input")`.
- **Fixtures are fake on purpose.** Every source ID contains `FIXTURE` (e.g. `pmid:FIXTURE-0001`) so test data can never pass for a real citation; a test enforces this.
- **Rules the contract enforces:** an `evidence` claim with no `source_ids` is rejected; if the model's output fails validation, `ctx.llm` retries once with the error, then raises `SchemaError`.
- **Real model calls (`LLM_MODE=live`)** go through the model router: it picks the model for your agent from `src/frontier/core/profiles.yaml`, caches responses so a repeated call is free, retries once on a 429 or 5xx and then tries the fallbacks, logs tokens and cost to `llm_calls`, and stops the run with `BudgetExceeded` at `MAX_CALLS_PER_RUN` or `MAX_USD_PER_RUN`. Before a live call works you need `NEBIUS_API_KEY` and `NEBIUS_BASE_URL` in `.env`, a price for each model in `src/frontier/core/prices.yaml` (the router refuses unpriced models so the USD cap holds), and the `llm_cache` table.
- **Not ready yet:** the real retrieval API (the stub stands in). It keeps the same interface, so your agent code won't change.

The top-level `data/` folder is for raw downloads only and is ignored by git. Never commit data files, `.env`, or generated memos and spreadsheets.

## Daily workflow

```bash
git checkout main && git pull
make setup                          # picks up any new packages a teammate added
git checkout -b yourname-feature    # e.g. priya-agent5
# ...work...
make test                           # must pass before you push
git push -u origin yourname-feature
```

Then open a pull request on GitHub into `main`. One teammate reviews it, CI must pass, then merge.

**Adding a package:** run `uv add <package>` (or `uv add --dev <package>` for test tools), never `pip install`. Commit both `pyproject.toml` and `uv.lock` in the same pull request, or everyone else's environment drifts from yours.

**Changing `agents/schemas.py`:** everyone codes against it, so those changes go through person 2.

**If something is broken after a pull:** `make rebuild` deletes your environment, rebuilds it from the lockfile and runs the tests.

## Run log

Every piece of work gets an entry in [runlog/](../runlog/): the prompt or request that started it, what was done, why, and how it was checked. This keeps the project auditable, including any AI-assisted work. See [runlog/README.md](../runlog/README.md) for the format. If you use Claude Code, the `runlog` skill in this repo writes the entry for you.

## Commands

| Command | What it does |
|---|---|
| `make setup` | Install the locked environment |
| `make rebuild` | Delete `.venv`, rebuild from `uv.lock`, run tests |
| `make up` / `make down` | Start / stop Postgres (data is kept between restarts) |
| `make schema` | Create tables on a fresh database |
| `make test` | Lint (ruff) and tests (pytest), no model calls |
| `make lint` | Auto-fix lint and format code |
| `make api` | API on http://localhost:8000 |
| `make ingest`, `make run`, `make backtest` | Not built yet |
