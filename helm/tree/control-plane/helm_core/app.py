"""Сборка приложения Control Plane.

Слушает только localhost (§4.6: Control Plane admin API не публикуется).
Наружу через Caddy проходят ровно /api/panel/v1/*, /auth/* и явные webhooks.
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .actions.fixtures import build_registry
from .approvals.service import ApprovalService
from .api import auth, internal, panel
from .api.security import SecurityHeadersMiddleware
from .config import Settings, get_settings, read_secret


def create_app(settings: Settings | None = None, *, service_secret: str | None = None,
              telegram_bot_token: str | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="HELM Control Plane", version="0.1.0",
                  docs_url=None, redoc_url=None, openapi_url=None)

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
    app.state.registry = build_registry(settings.policy_path)
    app.state.owner_id = settings.owner_id
    app.state.settings = settings
    app.state.service_secret = service_secret or read_secret("hermes_service_hmac", "")
    app.state.panel_auth_cookie_secret = read_secret("panel_auth_cookie_secret", "")
    app.state.telegram_bot_token = telegram_bot_token or read_secret("telegram_bot_token", "")

    def approval_service_factory(session):
        return ApprovalService(session, app.state.registry, owner_id=settings.owner_id)

    app.state.approval_service_factory = approval_service_factory

    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(internal.router)
    app.include_router(panel.router)
    app.include_router(auth.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
