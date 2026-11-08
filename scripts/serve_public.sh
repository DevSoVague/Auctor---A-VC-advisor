#!/usr/bin/env bash
# Serve the run API publicly for the Base44 app (owner: person 2).
#
#   scripts/serve_public.sh            # API on :8000 + a public https address via localtunnel
#
# Refuses to start without APP_API_TOKEN (read from .env): a public API without a token would
# let anyone start runs that spend our model credits. The Base44 app sends the token from a
# server-side function, never from the browser. Ctrl-C stops both the API and the tunnel.
set -euo pipefail
cd "$(dirname "$0")/.."

TOKEN="${APP_API_TOKEN:-$(grep -E '^APP_API_TOKEN=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)}"
if [ -z "$TOKEN" ]; then
  echo "APP_API_TOKEN is not set. Add a long random value to .env, e.g.:" >&2
  echo "  echo \"APP_API_TOKEN=\$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')\" >> .env" >&2
  exit 1
fi
PORT="${PORT:-8000}"

APP_API_TOKEN="$TOKEN" uv run --locked uvicorn frontier.api.main:app --port "$PORT" &
API_PID=$!
trap 'kill $API_PID $LT_PID 2>/dev/null || true' EXIT INT TERM

for _ in $(seq 1 60); do curl -s "localhost:$PORT/health" >/dev/null && break; sleep 0.5; done
echo "API up on http://localhost:$PORT"

# localtunnel comes from npm, so it works even when GitHub downloads are down.
npx --yes localtunnel@2 --port "$PORT" &
LT_PID=$!
echo "Waiting for the public address (shown below as 'your url is: https://...loca.lt')."
echo "Put that address in the Base44 app's API_BASE_URL secret."
wait $API_PID
