from agents.competitor.context import CompetitorCtx, load_fixture  # noqa: F401
from agents.competitor.records import TEAM_FIXTURES, StubRetrievalClient


class StubBackend:
    """Returns the team's <agent>.output.json fixture, whatever the messages say."""

    async def complete(self, *, run_id, agent, messages, schema) -> str:
        return (TEAM_FIXTURES / f"{agent}.output.json").read_text()


def make_context(config) -> CompetitorCtx:
    """A4/A5 context over the team's shared fixture records and stub model."""
    return CompetitorCtx(config=config, retrieval=StubRetrievalClient(config.as_of),
                         backend=StubBackend())
