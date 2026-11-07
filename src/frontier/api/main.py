"""FastAPI entry point. Run with `make api`.

Retrieval and run endpoints get mounted here as they are built
(see docs/PRD.md, Services).
"""

from fastapi import FastAPI

from frontier import __version__

app = FastAPI(title="Frontier Hypothesis Engine", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
