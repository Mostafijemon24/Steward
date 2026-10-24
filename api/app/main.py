"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, FastAPI

from app.core import observability
from app.core.config import get_settings
from app.core.deps import session_cookie_name, verify_csrf
from app.core.errors import install_error_handlers
from app.core.middleware import RequestContextMiddleware
from app.routers import auth, workspace


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.process_role != "api":
        raise RuntimeError(
            f"PROCESS_ROLE={settings.process_role} cannot serve HTTP: the API must run with the full production checks"
        )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    docs = not settings.is_production
    app = FastAPI(
        title="Steward API",
        version="0.5.0",
        description="Steward API: workspaces, tasks, approvals, files, audit, and the agent engine's control plane.",
        openapi_url="/api/v1/openapi.json" if docs else None,
        docs_url="/api/v1/docs" if docs else None,
        redoc_url=None,
    )
    install_error_handlers(app)

    v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_csrf)])
    v1.include_router(auth.router)
    v1.include_router(workspace.router)
    app.include_router(v1)

    observability.install(app)

    app.add_middleware(
        RequestContextMiddleware,
        max_body_bytes=settings.request_max_body_bytes,
        hsts=settings.is_production,
        session_cookie=session_cookie_name(settings),
    )
    app.add_middleware(observability.MetricsMiddleware)
    return app


app = create_app()
