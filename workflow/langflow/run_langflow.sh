#!/usr/bin/env bash
# Start Langflow with the Auctor components loaded, then import flows/auctor_analysis.json.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export AUCTOR_LANGFLOW_HOME="$HERE"
export FRONTIER_ENGINE_PATH="${FRONTIER_ENGINE_PATH:-$HERE/../..}"
export LANGFLOW_COMPONENTS_PATH="$HERE/components"
exec conda run --no-capture-output -n langflow langflow run --port "${PORT:-7860}"
