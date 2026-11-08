"""Settings from the environment (and ../frontier-engine/.env for the Nebius key)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(ROOT / ".env")
_load_env_file(ROOT.parent / ".env")                        # repo root (evidence-store vendored in the repo)
_load_env_file(ROOT.parent / "frontier-engine" / ".env")   # old layout: sibling folder

# Same default as the team repo (docker compose maps Postgres to 5433).
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://frontier:frontier@localhost:5433/frontier")
STORE_DIR = Path(os.environ.get("FRONTIER_STORE_DIR", ROOT / "data" / "frontier-store"))

NEBIUS_BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.us-central1.nebius.com/v1/")
NEBIUS_API_KEY = os.environ.get("NEBIUS_API_KEY", "")
# Embeddings can run on a separate Nebius account so they do not share the LLM agents' rate limit / quota.
# Unset -> same key and URL as the LLM.
EMBED_API_KEY = os.environ.get("EMBED_API_KEY", "").strip() or NEBIUS_API_KEY
EMBED_BASE_URL = os.environ.get("EMBED_BASE_URL", "").strip() or NEBIUS_BASE_URL
EMBED_MODEL = os.environ.get("EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")
EMBED_DIM = 4096
EXTRACT_MODEL = os.environ.get("EXTRACT_MODEL", "openai/gpt-oss-120b")

# Qwen3-Embedding: queries get an instruction, passages do not.
QUERY_INSTRUCTION = (
    "Given a question about COPD drugs, trials or mechanisms, "
    "retrieve passages from papers, trial registrations and drug labels that answer it"
)
