"""FastAPI application for the Agentic Self-Healing eSIM dashboard.

    python -m uvicorn backend.app:app --reload --port 8000

The app is only a transport. All behaviour comes from `esim_selfhealing/`,
which this layer imports without patching or subclassing it; `backend/services/`
adapts it and `backend/api/` exposes it. (The core itself has been changed by
the realism work - see REALISM.md - but not from here.)
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import public_router, router

app = FastAPI(
    title="Agentic Self-Healing eSIM / eUICC API",
    version="2.1.0",
    description=(
        "Read/act surface over the ReAct-MAPE-K loop: "
        "MONITOR -> REASON -> PLAN -> SAFETY -> ACT -> VERIFY -> LEARN. "
        "Real-time mode streams a prototype server/grid telemetry generator "
        "through the same loop; Simulation mode replays a scenario catalogue. "
        "Neither connects to live telecom infrastructure."
    ),
)

# Dev servers (Vite on 5173, 4173 preview, 5500 stdlib fallback) plus whatever
# origin(s) the deployment sets via CORS_ALLOW_ORIGINS (comma-separated). When
# the built frontend is served from this same FastAPI process (the normal
# "one public link" deployment - see README_DASHBOARD.md), the browser calls
# /api on the very origin it was loaded from and CORS does not enter into it
# at all; this list only matters if the frontend is ever hosted separately.
_default_origins = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:4173", "http://127.0.0.1:4173",
    "http://localhost:5500", "http://127.0.0.1:5500",
]
_extra_origins = [o.strip() for o in os.environ.get("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins + _extra_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(public_router)
app.include_router(router)


@app.get("/api")
def api_index():
    return {
        "name": "Agentic Self-Healing eSIM API",
        "mode": "DEMO / SIMULATION",
        "docs": "/docs",
    }


# -- optionally serve the built frontend -----------------------------------
# After `npm run build`, frontend/dist exists and the whole dashboard is served
# from this one process on port 8000. Before that, this block is simply skipped
# and the Vite dev server handles the UI.
_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        return FileResponse(_DIST / "index.html")
