#!/usr/bin/env bash
# One command: start Langflow, load the Person-3 step-by-step flow, open it. Run the workflow in the Playground.
#   ./run_langflow.sh            (PORT=7871 ./run_langflow.sh for another port; NO_OPEN=1 to not open the browser)
# Stop with Ctrl+C. Needs the conda env "langflow": bash ../../../scripts/setup_hack_env.sh creates it (and "hack").
#   CONDA_ENV=other ./run_langflow.sh   to use another env name
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LF="$HERE/langflow"
PORT="${PORT:-7870}"
CONDA_ENV="${CONDA_ENV:-langflow}"   # Python 3.11 env with Langflow; `hack` (3.12) runs the CLI and the tests
MON_PORT="${P3_MONITOR_PORT:-7872}"

# conda may not be on PATH in this shell: look in the usual places
if ! command -v conda >/dev/null 2>&1; then
  for c in /opt/homebrew/Caskroom/miniforge/base/bin/conda "$HOME/miniforge3/bin/conda" "$HOME/miniconda3/bin/conda" \
           "$HOME/anaconda3/bin/conda" /opt/miniconda3/bin/conda; do
    [ -x "$c" ] && export PATH="$(dirname "$c"):$PATH" && break
  done
fi
command -v conda >/dev/null 2>&1 || { echo "conda not found. Install it (brew install --cask miniforge), then: bash scripts/setup_hack_env.sh"; exit 1; }
conda env list | awk '{print $1}' | grep -qx "$CONDA_ENV" || { echo "conda env '$CONDA_ENV' not found. Create it: bash scripts/setup_hack_env.sh"; exit 1; }

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use (an old Langflow still running?). Close it, or use PORT=7871 $0"; exit 1
fi

export P3_LANGFLOW_HOME="$LF"
export P3_LANGGRAPH_HOME="$HERE"                                   # the nodes call THIS workflow's code
export FRONTIER_ENGINE_PATH="$HERE/.."   # always this one: a stale exported path broke the engine import
export LANGFLOW_COMPONENTS_PATH="$LF/components"
export PORT

echo "• building the flow from the current LangGraph steps…"
( cd "$LF" && conda run --no-capture-output -n "$CONDA_ENV" python gen_components.py >/dev/null \
            && conda run --no-capture-output -n "$CONDA_ENV" python build_steps_flow.py >/dev/null )

mkdir -p "$HERE/runs"
if lsof -nP -iTCP:"$MON_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "• monitor already running on :$MON_PORT"
else
  echo "• starting the live monitor on :$MON_PORT (open it before the first run)"
  ( cd "$HERE" && conda run --no-capture-output -n "$CONDA_ENV" python -m p3graph.live --port "$MON_PORT" ) >"$HERE/runs/monitor.log" 2>&1 &
  MONITOR=$!
fi

echo "• starting Langflow on :$PORT (first start takes about a minute)…"
conda run --no-capture-output -n "$CONDA_ENV" langflow run --port "$PORT" >"$HERE/runs/langflow.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER ${MONITOR:-} 2>/dev/null || true; pkill -P $SERVER 2>/dev/null || true; [ -n "${MONITOR:-}" ] && pkill -P $MONITOR 2>/dev/null || true' EXIT INT TERM

for i in $(seq 1 120); do
  curl -fs "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  kill -0 $SERVER 2>/dev/null || { echo "Langflow stopped. See runs/langflow.log"; exit 1; }
  sleep 2
done

echo "• loading the flow…"
OUT="$(cd "$LF" && conda run --no-capture-output -n "$CONDA_ENV" python sync_flow.py flows/person3_steps.json)"
echo "$OUT"
URL="$(echo "$OUT" | sed -n 's/^open: *//p')"
[ -z "${NO_OPEN:-}" ] && [ -n "$URL" ] && open "$URL" 2>/dev/null || true
echo "Live node monitor: http://127.0.0.1:$MON_PORT (already running)"
[ -z "${NO_OPEN:-}" ] && open "http://127.0.0.1:$MON_PORT" 2>/dev/null || true
echo
echo "Ready. In the canvas: set P3 · Setup (LLM mode stub|live, Search source), click Playground, type a message, send."
echo "Logs: $HERE/runs/langflow.log      Stop: Ctrl+C"
wait $SERVER
