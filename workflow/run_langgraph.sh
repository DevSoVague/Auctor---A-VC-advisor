#!/usr/bin/env bash
# Start LangGraph Studio (langgraph dev) on the Person-3 graph.  PORT=2035 ./run_langgraph.sh for another port.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export FRONTIER_ENGINE_PATH="$HERE/.."
cd "$HERE"
if ! conda run -n hack langgraph --version >/dev/null 2>&1; then
  echo "installing langgraph-cli into the hack env (once)..."
  conda run --no-capture-output -n hack pip install -q -U "langgraph-cli[inmem]"
fi
echo 'Studio input (stub, 0 tokens): {"config": {"as_of": "backtest", "mode": "single_shot"}, "llm": {"mode": "stub"}}'
exec conda run --no-capture-output -n hack langgraph dev --port "${PORT:-2034}"
