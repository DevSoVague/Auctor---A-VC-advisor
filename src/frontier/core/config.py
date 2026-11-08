"""Settings from the environment and .env, plus the YAML files in core/ (owner: person 2)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

CORE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CORE_DIR.parents[2]


class Settings(BaseSettings):
    """Every field can be set as an environment variable of the same name, upper-cased.

    Environment variables win over .env; .env is only read from the repo root.
    """

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql://frontier:frontier@localhost:5433/frontier"
    profile: Literal["dev", "final"] = "dev"
    llm_mode: Literal["live", "stub"] = "live"
    nebius_api_key: str = ""
    nebius_base_url: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    max_usd_per_run: float = 5.0
    max_calls_per_run: int = 300
    max_output_tokens: int = 8000
    temperature: float = 0.0  # deterministic by default, so reruns reproduce
    # Per model call. Long enough for slow reasoning models (Person 3 measured up to
    # ~195 s for one agent call on GLM-5.1), short enough that a stuck call can't
    # hold a run for LiteLLM's default 10 minutes. A timeout counts as retryable.
    request_timeout_seconds: float = 300.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml(name: str) -> dict:
    """Read core/<name>.yaml; an empty or all-comment file reads as {}."""
    return yaml.safe_load((CORE_DIR / f"{name}.yaml").read_text()) or {}
