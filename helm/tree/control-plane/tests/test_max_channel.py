"""Канал MAX (ТЗ §10) целиком: дедуп, /force, вебхук, очередь исходящих.

Проверяется сторона Control Plane. Плагин Hermes (`max-bridge`, ADR-020)
здесь заменён двойником: его контракт — «принять и вернуть управление
сразу», и именно это свойство тесты и фиксируют.
"""

import json
import ssl
import time
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from helm_core.api.security import sign
from helm_core.app import create_app
from helm_core.channels.max import (
    InboundMax, WEBHOOK_SECRET_HEADER, parse_message_created, verify_webhook_secret,
)
from helm_core.config import Settings
from helm_core.dispatch import BACKOFF, MAX_ATTEMPTS, deliver_pending
from helm_core.hermes_bridge import HermesUnavailable
from helm_core.ingest import CROSS_CHANNEL_WINDOW, strip_force_prefix
from helm_core.models import Base, ChannelEvent, OutboxMessage, Task, utcnow
from helm_core.outbox import enqueue

from conftest import DB_URL, OWNER_ID, POLICY_PATH

SERVICE_SECRET = "test-service-secret"
WEBHOOK_SECRET = "test-max-webhook-secret"
CHAT_ID = "777"

#: Идентификатор владельца в MAX — НЕ тот же, что conftest.OWNER_ID
#: (Telegram). Разные мессенджеры, разные пространства id; задачи при этом
#: заводятся под канонической identity, иначе §10.4 не сработает.
MAX_OWNER_ID = "5550001"


# ── §10.4: окно cross-channel дедупа ─────────────────────────────────────────

def test_cross_channel_window_matches_spec():
    """§10.4 задаёт ровно 2 минуты. Значение не подбирается «на глаз»."""
    assert CROSS_CHANNEL_WINDOW == timedelta(minutes=2)


def test_duplicate_outside_window_creates_new_task(session, ingest):
    tg = ingest.register(channel="telegram", external_message_id="tg-1",
                         owner_id=OWNER_ID, text="собери отчёт")
    session.flush()
    # Первое сообщение состарено за границу окна: владелец вернулся к той
    # же мысли спустя время, это новое намерение, а не дубль доставки.
    event = session.scalar(select(ChannelEvent).where(ChannelEvent.channel == "telegram"))
    event.received_at = utcnow() - CROSS_CHANNEL_WINDOW - timedelta(seconds=1)
    session.flush()

    mx = ingest.register(channel="max", external_message_id="max-1",
                         owner_id=OWNER_ID, text="собери отчёт")
    session.flush()

    assert mx.created is True
    assert mx.task.id != tg.task.id


# ── §10.4: «Явная команда /force создаёт новую task» ─────────────────────────

@pytest.mark.parametrize("raw, expected_text, expected_forced", [
    ("/force собери отчёт", "собери отчёт", True),
    ("/FORCE собери отчёт", "собери отчёт", True),
    ("  /force  собери отчёт  ", "собери отчёт", True),
    # Не команда: префикс должен быть отдельным словом и что-то нести.
    ("/forcemajeure случился", "/forcemajeure случился", False),
    ("/force", "/force", False),
    ("/force   ", "/force   ", False),
    ("собери отчёт /force", "собери отчёт /force", False),
])
def test_strip_force_prefix(raw, expected_text, expected_forced):
    assert strip_force_prefix(raw) == (expected_text, expected_forced)


def test_force_bypasses_cross_channel_dedup(session, ingest):
    tg = ingest.register(channel="telegram", external_message_id="tg-1",
                         owner_id=OWNER_ID, text="собери отчёт")
    session.flush()
    mx = ingest.register(channel="max", external_message_id="max-1",
                         owner_id=OWNER_ID, text="/force собери отчёт")
    session.flush()

    assert mx.created is True, "/force обязан создать новую задачу (§10.4)"
    assert mx.task.id != tg.task.id
    assert mx.text == "собери отчёт", "префикс не должен доходить до модели"
    assert len(session.scalars(select(Task)).all()) == 2


def test_force_does_not_bypass_same_message_id(session, ingest):
    """Переотправка апдейта транспортом — не повторная просьба владельца."""
    first = ingest.register(channel="max", external_message_id="max-1",
                            owner_id=OWNER_ID, text="/force собери отчёт")
    session.flush()
    second = ingest.register(channel="max", external_message_id="max-1",
                             owner_id=OWNER_ID, text="/force собери отчёт")
    session.flush()

    assert second.created is False
    assert second.dedup_reason == "same_external_message_id"
    assert first.task.id == second.task.id


# ── §10.1: разбор вебхука и секрет ───────────────────────────────────────────

def _update(text: str = "собери отчёт", *, mid: str = "mid.1",
            sender: object = int(MAX_OWNER_ID), chat: object = 777) -> dict:
    return {
        "update_type": "message_created",
        "message": {
            "sender": {"user_id": sender},
            "recipient": {"chat_id": chat, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text},
        },
    }


def test_parse_message_created_coerces_ids_to_str():
    parsed = parse_message_created(_update())
    assert parsed == InboundMax(text="собери отчёт", sender_id=MAX_OWNER_ID,
                                chat_id=CHAT_ID, message_id="mid.1")


@pytest.mark.parametrize("update", [
    {"update_type": "bot_added", "message": {}},
    {"update_type": "message_created", "message": {}},
    _update(text=""),
    {"update_type": "message_created",
     "message": {"sender": {}, "recipient": {"chat_id": 1}, "body": {"mid": "m", "text": "x"}}},
])
def test_parse_message_created_ignores_foreign_events(update):
    assert parse_message_created(update) is None


@pytest.mark.parametrize("provided, expected_ok", [
    (WEBHOOK_SECRET, True),
    ("другой-секрет", False),
    ("", False),
    (None, False),
    # Не-ASCII не должен ронять compare_digest TypeError'ом (F-260829-05).
    ("секрет-кириллицей", False),
])
def test_verify_webhook_secret(provided, expected_ok):
    assert verify_webhook_secret(WEBHOOK_SECRET, provided) is expected_ok


def test_verify_webhook_secret_fails_closed_without_configured_secret():
    """Секрет не задан (пустой дефолт read_secret) — не пускать никого."""
    assert verify_webhook_secret("", "что угодно") is False


def test_ssl_context_keeps_standard_roots():
    """Корень Минцифры добавляется К обычным, а не вместо них.

    Подмена всего набора связкой из двух сертификатов означала бы, что
    Control Plane перестал доверять любому обычному удостоверяющему
    центру. Проверяем на среде без связки — контекст обязан быть
    полноценным и здесь.
    """
    from helm_core.channels.max import RU_CA_BUNDLE, ssl_context

    assert not RU_CA_BUNDLE.exists(), "тест рассчитан на среду без связки"
    context = ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert len(context.get_ca_certs()) > 0, "стандартные корни не загружены"


# ── §10.1/§10.3: эндпоинт /hooks/max ─────────────────────────────────────────

class FakeBridge:
    """Двойник плагина max-bridge: принимает и сразу возвращает управление."""

    def __init__(self, available: bool = True):
        self.available = available
        self.calls: list[dict] = []

    def deliver(self, **kwargs) -> None:
        if not self.available:
            raise HermesUnavailable("двойник: chief недоступен")
        self.calls.append(kwargs)


@pytest.fixture
def app(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    settings = Settings(database_url=DB_URL, policy_path=POLICY_PATH, owner_id=OWNER_ID,
                        max_owner_id=MAX_OWNER_ID)
    application = create_app(settings, service_secret=SERVICE_SECRET)
    application.state.max_webhook_secret = WEBHOOK_SECRET
    application.state.hermes_bridge = FakeBridge()
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def post_hook(client, update: dict, *, secret: str | None = WEBHOOK_SECRET):
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers[WEBHOOK_SECRET_HEADER] = secret
    return client.post("/hooks/max", content=json.dumps(update).encode(), headers=headers)


def test_webhook_rejects_wrong_secret(app, client):
    # Секрет здесь ASCII не случайно: HTTP-заголовок с кириллицей нельзя
    # даже отправить (httpx падает на кодировании), то есть до сервера он
    # не дойдёт. Устойчивость самой проверки к не-ASCII — отдельным
    # юнит-тестом на verify_webhook_secret выше.
    response = post_hook(client, _update(), secret="wrong-secret")
    assert response.status_code == 403
    assert app.state.hermes_bridge.calls == []
    with app.state.session_factory() as session:
        assert session.scalars(select(Task)).all() == []


def test_webhook_rejects_missing_secret_header(app, client):
    assert post_hook(client, _update(), secret=None).status_code == 403
    assert app.state.hermes_bridge.calls == []


def test_webhook_registers_task_and_calls_chief(app, client):
    response = post_hook(client, _update())

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "accepted"
    with app.state.session_factory() as session:
        task = session.scalars(select(Task)).one()
        assert task.origin_channel == "max"
        assert task.status == "REGISTERED"
    call = app.state.hermes_bridge.calls[0]
    assert call["channel"] == "max"
    assert call["chat_id"] == CHAT_ID
    assert call["text"] == "собери отчёт"
    assert call["task_id"] == response.json()["task_id"]


def test_webhook_rejects_non_owner(app, client):
    response = post_hook(client, _update(sender=999999))
    assert response.status_code == 403
    assert app.state.hermes_bridge.calls == []


def test_webhook_rejects_telegram_owner_id_sent_as_max_sender(app, client):
    """Идентификаторы каналов не взаимозаменяемы: id из Telegram здесь чужой."""
    response = post_hook(client, _update(sender=OWNER_ID))
    assert response.status_code == 403


def test_webhook_fails_closed_without_configured_max_owner(app, client):
    app.state.max_owner_id = ""
    assert post_hook(client, _update()).status_code == 403
    assert app.state.hermes_bridge.calls == []


def test_webhook_registers_task_under_canonical_owner_identity(app, client):
    """§10.4 работает только если задача из MAX заведена под тем же owner_id.

    Иначе normalized_hash одного и того же вопроса из Telegram и MAX
    разойдётся, и cross-channel дедуп не сработает никогда.
    """
    assert post_hook(client, _update()).status_code == 202
    with app.state.session_factory() as session:
        task = session.scalars(select(Task)).one()
        assert task.origin_owner_id == OWNER_ID


def test_webhook_ignores_foreign_update_type(app, client):
    response = post_hook(client, {"update_type": "bot_added"})
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert app.state.hermes_bridge.calls == []


def test_webhook_collapses_cross_channel_duplicate_silently(app, client):
    """Решение владельца 29.08.2026: схлопнутый дубль остаётся без ответа.

    Ответ на этот вопрос владелец уже получает в Telegram; второй ответ
    в MAX — шум. Ключевое здесь — chief НЕ вызывается повторно.
    """
    with app.state.session_factory() as session:
        from helm_core.ingest import IngestService
        IngestService(session, owner_id=OWNER_ID).register(
            channel="telegram", external_message_id="tg-1",
            owner_id=OWNER_ID, text="собери отчёт")
        session.commit()

    response = post_hook(client, _update())

    assert response.status_code == 200
    assert response.json()["status"] == "collapsed"
    assert app.state.hermes_bridge.calls == []
    with app.state.session_factory() as session:
        assert len(session.scalars(select(Task)).all()) == 1
        assert session.scalars(select(OutboxMessage)).all() == []


def test_webhook_does_not_call_chief_twice_on_redelivery(app, client):
    assert post_hook(client, _update()).status_code == 202
    second = post_hook(client, _update())

    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert len(app.state.hermes_bridge.calls) == 1


def test_webhook_queues_transport_notice_when_chief_is_down(app, client):
    """§10.3: task остаётся REGISTERED, владелец получает транспортное уведомление."""
    app.state.hermes_bridge = FakeBridge(available=False)

    response = post_hook(client, _update())

    assert response.status_code == 200
    assert response.json()["status"] == "accepted_hermes_down"
    with app.state.session_factory() as session:
        task = session.scalars(select(Task)).one()
        assert task.status == "REGISTERED"
        message = session.scalars(select(OutboxMessage)).one()
        assert message.channel == "max"
        assert message.recipient == CHAT_ID
        assert message.status == "PENDING"
        assert "недоступен" in message.payload_reference["text"]


# ── §10.3/§30.2: очередь исходящих и доставка ────────────────────────────────

def post_internal(client, path: str, payload: dict):
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    return client.post(path, content=body, headers={
        "Content-Type": "application/json", "X-Helm-Timestamp": ts,
        "X-Helm-Signature": sign(SERVICE_SECRET, ts, body)})


def test_outbound_enqueues_reply(app, client):
    response = post_internal(client, "/internal/outbound", {
        "channel": "max", "recipient": CHAT_ID, "reference": "task-1", "text": "готово"})

    assert response.status_code == 201, response.text
    assert response.json()["created"] is True
    with app.state.session_factory() as session:
        message = session.scalars(select(OutboxMessage)).one()
        assert message.payload_reference["text"] == "готово"


def test_outbound_same_reference_does_not_duplicate(app, client):
    payload = {"channel": "max", "recipient": CHAT_ID, "reference": "task-1", "text": "готово"}
    assert post_internal(client, "/internal/outbound", payload).json()["created"] is True
    assert post_internal(client, "/internal/outbound", payload).json()["created"] is False
    with app.state.session_factory() as session:
        assert len(session.scalars(select(OutboxMessage)).all()) == 1


def test_outbound_requires_service_auth(client):
    response = client.post("/internal/outbound", json={
        "channel": "max", "recipient": CHAT_ID, "reference": "r", "text": "t"})
    assert response.status_code == 422  # заголовки подписи обязательны


class FakeSender:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[tuple[str, str]] = []

    def __call__(self, recipient: str, text: str) -> None:
        if self.fail:
            raise RuntimeError("двойник: канал недоступен")
        self.sent.append((recipient, text))


def _queue(session, *, reference: str = "r1", text: str = "готово") -> OutboxMessage:
    result = enqueue(session, channel="max", recipient=CHAT_ID, reference=reference,
                     payload_reference={"text": text})
    session.commit()
    return result.message


def test_deliver_pending_sends_and_clears_payload(session):
    message = _queue(session)
    sender = FakeSender()

    report = deliver_pending(session, {"max": sender})

    assert report.sent == 1
    assert sender.sent == [(CHAT_ID, "готово")]
    session.refresh(message)
    assert message.status == "SENT"
    # §10.2: доставленный текст в Control Plane не остаётся.
    assert message.payload_reference is None


def test_deliver_pending_retries_with_backoff(session):
    message = _queue(session)
    before = utcnow()

    report = deliver_pending(session, {"max": FakeSender(fail=True)})

    assert (report.retried, report.sent) == (1, 0)
    session.refresh(message)
    assert message.status == "PENDING"
    assert message.attempts == 1
    assert message.next_attempt_at >= before + BACKOFF[0]


def test_deliver_pending_skips_message_not_yet_due(session):
    message = _queue(session)
    message.next_attempt_at = utcnow() + timedelta(minutes=5)
    session.commit()

    report = deliver_pending(session, {"max": FakeSender()})

    assert report == type(report)()  # ничего не тронуто
    session.refresh(message)
    assert message.status == "PENDING"


def test_deliver_pending_gives_up_after_max_attempts(session):
    message = _queue(session)
    message.attempts = MAX_ATTEMPTS - 1
    session.commit()

    report = deliver_pending(session, {"max": FakeSender(fail=True)})

    assert report.failed == 1
    session.refresh(message)
    assert message.status == "FAILED"


def test_deliver_pending_fails_unknown_channel(session):
    message = _queue(session)

    report = deliver_pending(session, {})

    assert report.failed == 1
    session.refresh(message)
    assert message.status == "FAILED", "строка без отправителя не должна крутиться вечно"


def test_deliver_pending_delivers_each_message_once(session):
    _queue(session, reference="r1", text="первый")
    _queue(session, reference="r2", text="второй")
    sender = FakeSender()

    first = deliver_pending(session, {"max": sender})
    second = deliver_pending(session, {"max": sender})

    assert first.sent == 2
    assert second.sent == 0, "§30.2: outbox no duplicate"
    assert len(sender.sent) == 2
