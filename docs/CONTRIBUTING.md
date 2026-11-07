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
