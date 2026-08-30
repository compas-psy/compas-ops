"""Сборка приложения Control Plane.

Слушает только localhost (§4.6: Control Plane admin API не публикуется).
Наружу через Caddy проходят ровно /api/panel/v1/*, /auth/* и явные webhooks.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .actions.fixtures import build_registry
from .approvals.service import ApprovalService
from .api import auth, hooks, hooks_knowledge_telegram, internal, panel
from .api.security import SecurityHeadersMiddleware
from .channels.max import MaxSender
from .channels.telegram import TelegramSender
from .config import Settings, get_settings, read_secret
from .dispatch import deliver_pending
from .hermes_bridge import DEFAULT_URL as HERMES_BRIDGE_URL, HermesBridge


def create_app(settings: Settings | None = None, *, service_secret: str | None = None,
              telegram_bot_token: str | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="HELM Control Plane", version="0.1.0",
                  docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=_lifespan)

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
    app.state.registry = build_registry(settings.policy_path)
    app.state.owner_id = settings.owner_id
    app.state.settings = settings
    app.state.service_secret = service_secret or read_secret("hermes_service_hmac", "")
    app.state.panel_auth_cookie_secret = read_secret("panel_auth_cookie_secret", "")
    app.state.telegram_bot_token = telegram_bot_token or read_secret("telegram_bot_token", "")
    app.state.max_webhook_secret = read_secret("max_webhook_secret", "")
    app.state.max_owner_id = settings.max_owner_id
    #: v3.8 §9.0/P8.6.2 — bot token/webhook-секрет ОТДЕЛЬНЫЕ от owner
    #: chief bot (`telegram_bot_token`/`max_webhook_secret` выше): этот
    #: адаптер не должен получить возможность действовать от имени
    #: владельца, даже случайно (§14.18 "Dedicated Knowledge Bot process
    #: receives only its Telegram token + Control Plane service
    #: credential"). Пустая строка по умолчанию — тот же принцип, что у
    #: `max_bot_token`/`telegram_bot_token`: реализация не блокируется
    #: отсутствием реального токена, вебхук просто не может быть вызван
    #: успешно (secret compare с пустой строкой = verify_webhook_secret
    #: fail-closed) до того, как владелец заведёт бота через BotFather.
    app.state.knowledge_telegram_bot_token = read_secret("knowledge_telegram_bot_token", "")
    app.state.knowledge_telegram_webhook_secret = read_secret(
        "knowledge_telegram_webhook_secret", "")
    app.state.hermes_bridge = HermesBridge(HERMES_BRIDGE_URL, read_secret("hermes_api_server_key", ""))
    #: Отправители по каналам для доставщика outbox. Обычный ответ
    #: владельцу в Telegram по-прежнему уходит синхронно через adapter
    #: Hermes, а не через эту очередь — но `helm-knowledge-worker`
    #: (отдельный контейнер, без связи с Hermes gateway) может доставить
    #: уведомление о завершении разбора файла только сюда (найдено живьём
    #: 30.08.2026: без "telegram" в этом словаре доставщик молча помечал
    #: такие сообщения FAILED, см. `channels/telegram.py`).
    app.state.senders = {
        "max": MaxSender(read_secret("max_bot_token", "")),
        "telegram": TelegramSender(app.state.telegram_bot_token),
        "telegram_knowledge": TelegramSender(app.state.knowledge_telegram_bot_token),
    }

    def approval_service_factory(session):
        return ApprovalService(session, app.state.registry, owner_id=settings.owner_id)

    app.state.approval_service_factory = approval_service_factory

    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(internal.router)
    app.include_router(panel.router)
    app.include_router(auth.router)
    app.include_router(hooks.router)
    app.include_router(hooks_knowledge_telegram.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    """Доставщик исходящих (§10.3) живёт столько же, сколько приложение.

    Внутри процесса Control Plane, а не отдельным сервисом: очередь у него
    в той же БД, а воркер у uvicorn один (Dockerfile, --workers 1) — двух
    доставщиков не возникает, и координация между ними не нужна.
    """
    task = asyncio.create_task(_dispatch_loop(app))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


#: Пауза между разборами очереди. Пять секунд — задержка, незаметная в
#: переписке, и при этом 12 пустых запросов в минуту к локальной БД.
DISPATCH_INTERVAL_SECONDS = 5


async def _dispatch_loop(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(DISPATCH_INTERVAL_SECONDS)
        try:
            # to_thread: deliver_pending синхронный (SQLAlchemy + urllib) и
            # блокировал бы event loop вместе со всеми HTTP-запросами.
            await asyncio.to_thread(_dispatch_once, app)
        except Exception:
            # Цикл доставки не имеет права умереть от одной ошибки: тогда
            # очередь встанет молча до следующего рестарта контейнера.
            continue


def _dispatch_once(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        deliver_pending(session, app.state.senders)
