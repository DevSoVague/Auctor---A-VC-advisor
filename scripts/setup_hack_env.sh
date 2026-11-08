#!/usr/bin/env bash
# Two conda envs, as Person 3 set them up (their pinned versions conflict, so they cannot share one):
#
#   hack      Python 3.12  engine, LangGraph pipeline, agent 8 (memo), tests, the CLI     requirements.txt
#   langflow  Python 3.11  the Langflow canvas (runs the same agents + agent 8 as nodes)  requirements-langflow.txt
#
#   bash scripts/setup_hack_env.sh              # both
#   ONLY=hack bash scripts/setup_hack_env.sh    # just the engine env (fast)
#   ONLY=langflow bash scripts/setup_hack_env.sh
#
# Needs conda (Miniforge: `brew install --cask miniforge`). Safe to rerun: an existing env is updated.
# Each env also gets THIS repo (the `frontier` package: agent 8 and the run API) with `pip install -e --no-deps`,
# because the pinned lists already hold its dependencies.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
P3="$HERE/person3/grounded1to8"

if ! command -v conda >/dev/null 2>&1; then
  for c in /opt/homebrew/Caskroom/miniforge/base/bin/conda "$HOME/miniforge3/bin/conda" "$HOME/miniconda3/bin/conda" \
           "$HOME/anaconda3/bin/conda" /opt/miniconda3/bin/conda; do
    [ -x "$c" ] && export PATH="$(dirname "$c"):$PATH" && break
  done
fi
command -v conda >/dev/null 2>&1 || { echo "conda not found. Install it: brew install --cask miniforge"; exit 1; }

make_env() {  # name python requirements-file
  local env="$1" py="$2" reqs="$3"
  if conda env list | awk '{print $1}' | grep -qx "$env"; then
    echo "• conda env '$env' exists: updating it"
  else
    echo "• creating conda env '$env' (python $py)"
    conda create -n "$env" "python=$py" -y >/dev/null
  fi
  echo "• [$env] installing $(basename "$reqs") (exact versions; this takes a while)"
  conda run --no-capture-output -n "$env" python -m pip install -q -r "$reqs"
  echo "• [$env] installing this repo (frontier: agent 8 + run API) without touching the pins"
  conda run --no-capture-output -n "$env" python -m pip install -q --no-deps -e "$HERE"
}

if [ "${ONLY:-}" != "langflow" ]; then
  make_env hack 3.12 "$P3/requirements.txt"
  conda run --no-capture-output -n hack python - <<'PY'
import importlib
for m in ("pydantic", "openai", "langgraph", "psycopg", "pgvector", "docx", "openpyxl", "matplotlib", "frontier.memo.run_memo"):
    importlib.import_module(m)
print("  [hack] engine + agent 8 imports OK")
PY
fi
if [ "${ONLY:-}" != "hack" ]; then
  # Person 3's list is a snapshot of a working env, and one line in it (cv-tailor) cannot be installed from PyPI,
  # so it is used as a set of version CONSTRAINTS: pip installs Langflow and what the agents need, all at their pins.
  if conda env list | awk '{print $1}' | grep -qx langflow; then echo "• conda env 'langflow' exists: updating it"
  else echo "• creating conda env 'langflow' (python 3.11)"; conda create -n langflow python=3.11 -y >/dev/null; fi
  echo "• [langflow] installing Langflow 1.8.4 and the engine's needs with uv (pip's resolver gets lost in Langflow's own conflicts)"
  conda run --no-capture-output -n langflow python -m pip install -q uv
  LFPY="$(conda run -n langflow python -c 'import sys; print(sys.executable)')"
  # first at Person 3's pinned versions (constraints); if those do not resolve on today's PyPI, with uv's own resolution
  PKGS=("langflow==1.8.4" "langgraph==0.6.11" pgvector "psycopg[binary]" aiosqlite python-docx openpyxl matplotlib litellm pydantic-settings PyYAML)
  conda run --no-capture-output -n langflow uv pip install --python "$LFPY" "${PKGS[@]}" -c "$P3/requirements-langflow.txt" || {
    echo "• [langflow] Person 3's exact pins do not resolve together today; installing with uv's own resolution"
    conda run --no-capture-output -n langflow uv pip install --python "$LFPY" "${PKGS[@]}"
  }
  echo "• [langflow] installing this repo (frontier: agent 8) without touching the pins"
  conda run --no-capture-output -n langflow python -m pip install -q --no-deps -e "$HERE"
  conda run --no-capture-output -n langflow python - <<'PY'
import importlib
for m in ("lfx", "langgraph", "pgvector", "docx", "frontier.memo.run_memo"):
    importlib.import_module(m)
print("  [langflow] langflow + engine + agent 8 imports OK")
PY
fi
echo
echo "Done. Next:  bash scripts/run_langflow_monitor.sh"
