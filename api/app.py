"""Сборка приложения: маршруты /api/* и (если есть) статический фронтенд из web/ на корневом адресе."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from core.config import Settings

from .jobs import JobBusy
from .routers import archive, ask, facts, report, system
from .state import AppState

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(settings: Settings | None = None, llm: Any = None) -> FastAPI:
    app = FastAPI(title="DocAnalyzer AMR/RMS", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.core = AppState(settings, llm)

    @app.exception_handler(JobBusy)
    async def _busy(_: Request, exc: JobBusy):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    for module in (system, archive, ask, facts, report):
        app.include_router(module.router, prefix="/api")

    if (WEB_DIR / "index.html").exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
