#!/usr/bin/env bash
# One command: start Langflow, load the Person-3 step-by-step flow, open it. Run the workflow in the Playground.
#   ./run_langflow.sh            (PORT=7861 ./run_langflow.sh for another port; NO_OPEN=1 to not open the browser)
# Stop with Ctrl+C. Needs the conda env "langflow".
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LF="$HERE/langflow"
PORT="${PORT:-7860}"

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use (an old Langflow still running?). Close it, or use PORT=7861 $0"; exit 1
fi

export P3_LANGFLOW_HOME="$LF"
export P3_LANGGRAPH_HOME="$HERE"                                   # the nodes call THIS workflow's code
export FRONTIER_ENGINE_PATH="$HERE/../frontier-engine"   # always this one: a stale exported path broke the engine import
export LANGFLOW_COMPONENTS_PATH="$LF/components"
export PORT

echo "• building the flow from the current LangGraph steps…"
( cd "$LF" && conda run --no-capture-output -n langflow python gen_components.py >/dev/null \
            && conda run --no-capture-output -n langflow python build_steps_flow.py >/dev/null )

echo "• starting Langflow on :$PORT (first start takes about a minute)…"
conda run --no-capture-output -n langflow langflow run --port "$PORT" >"$HERE/runs/langflow.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true; pkill -P $SERVER 2>/dev/null || true' EXIT INT TERM

for i in $(seq 1 120); do
  curl -fs "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  kill -0 $SERVER 2>/dev/null || { echo "Langflow stopped. See runs/langflow.log"; exit 1; }
  sleep 2
done

echo "• loading the flow…"
OUT="$(cd "$LF" && conda run --no-capture-output -n langflow python sync_flow.py flows/person3_steps.json)"
echo "$OUT"
URL="$(echo "$OUT" | sed -n 's/^open: *//p')"
[ -z "${NO_OPEN:-}" ] && [ -n "$URL" ] && open "$URL" 2>/dev/null || true
echo "Live node monitor: http://127.0.0.1:${P3_MONITOR_PORT:-7862} (starts with the first run)"
[ -z "${NO_OPEN:-}" ] && open "http://127.0.0.1:${P3_MONITOR_PORT:-7862}" 2>/dev/null || true
echo
echo "Ready. In the canvas: set P3 · Setup (LLM mode stub|live, Search source), click Playground, type a message, send."
echo "Logs: $HERE/runs/langflow.log      Stop: Ctrl+C"
wait $SERVER
