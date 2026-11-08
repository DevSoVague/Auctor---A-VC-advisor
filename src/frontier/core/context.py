"""RunContext: what every agent receives as `ctx` (owner: person 2).

    async def run(inp: A3Input, ctx: RunContext) -> ProductProfile:
        hits = await ctx.retrieval.search("PDE4 COPD exacerbations")
        return await ctx.llm(agent="a3_profile", messages=[...], schema=ProductProfile)

With LLM_MODE=stub (what `make test` sets), ctx.llm returns the agent's fixture
output from tests/fixtures/<agent>.output.json and makes no model call. With
LLM_MODE=live, calls go through the model router (core/router.py).
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from frontier.agents.schemas import RunConfig
from frontier.core.config import Settings
from frontier.core.retrieval import RetrievalClient, StubRetrievalClient, fixtures_dir
from frontier.core.router import Router

T = TypeVar("T", bound=BaseModel)
Message = dict[str, str]


class SchemaError(RuntimeError):
    """The model's output failed schema validation twice."""


class LLMBackend(Protocol):
    """Returns the model's raw JSON text. The router implements this for live calls."""

    async def complete(
        self, *, run_id: str, agent: str, messages: list[Message], schema: type[BaseModel]
    ) -> str: ...


class StubLLM:
    """Returns tests/fixtures/<agent>.output.json, whatever the messages say."""

    async def complete(
        self, *, run_id: str, agent: str, messages: list[Message], schema: type[BaseModel]
    ) -> str:
        path = fixtures_dir() / f"{agent}.output.json"
        if not path.exists():
            raise FileNotFoundError(f"LLM_MODE=stub but there is no fixture at {path}")
        return path.read_text()


@dataclass
class RunContext:
    config: RunConfig
    retrieval: RetrievalClient
    backend: LLMBackend
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    async def llm(self, *, agent: str, messages: list[Message], schema: type[T]) -> T:
        """Call the model and return a validated `schema` instance.

        If the output fails validation, retry once with the error appended,
        then raise SchemaError (the PRD's "retry once, then fail loudly").
        """
        attempt = list(messages)
        for try_number in (1, 2):
            raw = await self.backend.complete(
                run_id=self.run_id, agent=agent, messages=attempt, schema=schema
            )
            try:
                return schema.model_validate_json(raw)
            except ValidationError as err:
                if try_number == 2:
                    raise SchemaError(
                        f"{agent}: output failed {schema.__name__} validation twice:\n{err}"
                    ) from err
                attempt = [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": "Your JSON failed schema validation. Fix it and reply "
                        f"with JSON only.\n{err}",
                    },
                ]
        raise AssertionError("unreachable")


def make_context(config: RunConfig, run_id: str | None = None) -> RunContext:
    """Build a RunContext for config, choosing stub or live from LLM_MODE (env, then .env)."""
    settings = Settings()
    return RunContext(
        config=config,
        # Fixture records until the retrieval API lands (PRD build order step 5).
        retrieval=StubRetrievalClient(config.as_of),
        backend=StubLLM() if settings.llm_mode == "stub" else Router.from_settings(settings),
        run_id=run_id or str(uuid.uuid4()),
    )


def load_fixture(name: str) -> dict:
    """Read tests/fixtures/<name>.json, e.g. load_fixture('a3_profile.input')."""
    return json.loads((fixtures_dir() / f"{name}.json").read_text())
