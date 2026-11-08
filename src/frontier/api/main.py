"""FastAPI entry point. Run with `make api`.

Serves the run API (api/runs.py) that the Base44 app calls. FRONTEND_ORIGINS
(comma-separated) limits which sites may call it from a browser; default '*'.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from frontier import __version__
from frontier.api.runs import lifespan
from frontier.api.runs import router as runs_router

app = FastAPI(title="Frontier Hypothesis Engine", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("FRONTEND_ORIGINS", "*").split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(runs_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
