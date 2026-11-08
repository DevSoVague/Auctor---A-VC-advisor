#!/usr/bin/env bash
# One command: Langflow canvas on http://localhost:7870 and the live monitor on http://127.0.0.1:7872.
#
#   bash scripts/run_langflow_monitor.sh
#
# In the canvas: click "P3 · Setup" (LLM mode stub|live, search source), open the Playground, type "run", send.
# Every node lights up in the canvas; the monitor shows each step, each model call and agent 8 (the memo).
# Stop with Ctrl+C. Needs: bash scripts/setup_hack_env.sh (once), Docker/Postgres for a live run on the real store.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
P3="$HERE/person3/grounded1to8/langgraph-person3"

if [ ! -f "$HERE/.env" ]; then echo "• no .env yet: copy .env.example to .env and add your NEBIUS_API_KEY (stub runs work without it)"; fi
if command -v docker >/dev/null 2>&1 && ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q db; then
  echo "• the evidence store (Postgres) is not running: for a live run on the real store: make up   (stub runs do not need it)"
fi
# the engine reads the repo-root .env itself; make the repo's frontier package and the pipeline find each other
export P3_PIPELINE="${P3_PIPELINE:-grounded1to8}"
cd "$P3" && exec ./run_langflow.sh
