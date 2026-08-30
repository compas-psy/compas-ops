"""Канал MAX (ТЗ §10) целиком: дедуп, /force, вебхук, очередь исходящих.

Проверяется сторона Control Plane. Встроенный API-сервер Hermes
(`/v1/responses`, §10.2, ADR-020) здесь заменён двойником HermesBridge —
реальный HTTP-вызов проверяется отдельно, живым смоуком на сервере
(`scripts/hermes-responses-diagnose.sh`).
"""

import json
import ssl
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from helm_core.api.security import sign
from helm_core.app import create_app
from helm_core.channels.max import (
    InboundMax, MaxAttachmentUnsupported, MaxSender, WEBHOOK_SECRET_HEADER,
    download_attachment, parse_attachment, parse_message_created, verify_webhook_secret,
)
from helm_core.config import Settings
from helm_core.dispatch import BACKOFF, MAX_ATTEMPTS, deliver_pending
from helm_core.hermes_bridge import HermesUnavailable
from helm_core.ingest import CROSS_CHANNEL_WINDOW, strip_force_prefix
from helm_core.knowledge.rls import apply_rls
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (
    Base, ChannelEvent, KnowledgeBatchItem, KnowledgeIngestBatch, KnowledgePendingAttachment,
    KnowledgeSource, OutboxMessage, Task, utcnow,
)
from helm_core.outbox import enqueue

from conftest import DB_URL, OWNER_ID, POLICY_PATH, seed_system_owner

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


# ── P8.5.7: вложения ──────────────────────────────────────────────────────

def test_parse_message_created_accepts_attachment_without_text():
    """Раньше отсутствие text отбрасывало ЛЮБОЕ сообщение — включая файл
    без подписи (P8.5.7 меняет это, не трогая случай пустого текста БЕЗ
    вложения — см. test_parse_message_created_ignores_foreign_events)."""
    update = _update(text="")
    update["message"]["body"]["attachments"] = [{"type": "file", "payload": {"url": "x"}}]

    parsed = parse_message_created(update)

    assert parsed is not None
    assert parsed.text == ""
    assert parsed.attachments == [{"type": "file", "payload": {"url": "x"}}]


def test_parse_attachment_extracts_url():
    attachment = {"type": "file", "filename": "report.pdf",
                 "payload": {"url": "https://cdn.max.ru/f/abc"}}

    parsed = parse_attachment(attachment)

    assert parsed.kind == "file"
    assert parsed.filename == "report.pdf"
    assert parsed.url == "https://cdn.max.ru/f/abc"


def test_parse_attachment_unknown_shape_raises_with_field_names_not_values():
    attachment = {"type": "file", "payload": {"token": "secret-token-value"}}

    with pytest.raises(MaxAttachmentUnsupported) as exc_info:
        parse_attachment(attachment)

    assert exc_info.value.payload_keys == ["token"]
    assert "secret-token-value" not in str(exc_info.value)


def test_download_attachment_fetches_bytes():
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"file bytes"

    with patch("urllib.request.urlopen", return_value=_FakeResponse()):
        data = download_attachment("https://cdn.max.ru/f/abc")

    assert data == b"file bytes"


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


def test_max_sender_puts_chat_id_in_query_not_body():
    """Проверено вживую 29.08.2026: MAX не читает chat_id из тела вовсе
    и отвечает 400 'Unknown recipient' на синтаксически валидный запрос
    с ним в теле — правильная форма унаследована от TamTam Bot API.
    Регрессия сюда означала бы, что канал MAX снова молча не отправляет
    ничего, отчитываясь лишь FAILED-статусом после исчерпания ретраев.
    """
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout, context):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["auth"] = request.get_header("Authorization")
        return FakeResponse()

    with patch("helm_core.channels.max.urllib.request.urlopen", fake_urlopen):
        MaxSender("bot-token")(CHAT_ID, "готово")

    assert captured["url"] == f"https://platform-api2.max.ru/messages?chat_id={CHAT_ID}"
    assert captured["body"] == {"text": "готово"}
    assert "chat_id" not in captured["body"]
    assert captured["auth"] == "bot-token"


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
    """Двойник HermesBridge: не ходит по сети, помнит вызовы."""

    def __init__(self, available: bool = True, reply: str = "готово"):
        self.available = available
        self.reply = reply
        self.calls: list[dict] = []

    def deliver(self, **kwargs) -> str:
        self.calls.append(kwargs)
        if not self.available:
            raise HermesUnavailable("двойник: chief недоступен")
        return self.reply


@pytest.fixture
def app(engine, tmp_path, monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_rls(conn)
    seed_system_owner(engine)
    settings = Settings(database_url=DB_URL, policy_path=POLICY_PATH, owner_id=OWNER_ID,
                        max_owner_id=MAX_OWNER_ID)
    application = create_app(settings, service_secret=SERVICE_SECRET)
    application.state.max_webhook_secret = WEBHOOK_SECRET
    application.state.hermes_bridge = FakeBridge()
    # НАЙДЕНО 30.08.2026: hooks.py зовёт stage_attachment()/resolve_pending_
    # domain() без spool_root/vault_root — реальные тесты вложений писали в
    # /opt/helm-state/knowledge-spool и /opt/helm-knowledge на ЭТОЙ машине
    # (песочница разработки, не боевой сервер, но всё равно грязно —
    # десятки файлов-мусора накопились за сессию). Значения по умолчанию
    # у этих функций связаны на момент def (Python не перечитывает module-level константу
    # при каждом вызове) — monkeypatch самой DEFAULT_SPOOL_ROOT в
    # chat_intake ничего бы не дал; подменяем сами имена в hooks.py.
    import helm_core.api.hooks as hooks_module
    spool_root = str(tmp_path / "spool")
    vault_root = str(tmp_path / "vault")
    real_stage = hooks_module.stage_attachment
    real_resolve = hooks_module.resolve_pending_domain
    monkeypatch.setattr(hooks_module, "stage_attachment",
                        lambda *a, **kw: real_stage(*a, spool_root=spool_root, **kw))
    monkeypatch.setattr(hooks_module, "resolve_pending_domain",
                        lambda *a, **kw: real_resolve(*a, vault_root=vault_root, **kw))
    # То же самое для ZIP-batch (P8.5.2.1, v3.7) — raw_batches_root/
    # vault_root по умолчанию тоже /opt/helm-knowledge/..., та же утечка.
    raw_batches_root = str(tmp_path / "raw-batches")
    real_stage_batch = hooks_module.stage_batch
    real_resolve_batch = hooks_module.resolve_batch_domain
    monkeypatch.setattr(hooks_module, "stage_batch",
                        lambda *a, **kw: real_stage_batch(*a, raw_batches_root=raw_batches_root, **kw))
    monkeypatch.setattr(hooks_module, "resolve_batch_domain",
                        lambda *a, **kw: real_resolve_batch(*a, vault_root=vault_root, **kw))
    # P8.5.12: та же утечка — try_remember() пишет Markdown-зеркало под
    # DEFAULT_VAULT_ROOT по умолчанию.
    real_try_remember = hooks_module.try_remember
    monkeypatch.setattr(hooks_module, "try_remember",
                        lambda *a, **kw: real_try_remember(*a, vault_root=vault_root, **kw))
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
        message = session.scalars(select(OutboxMessage)).one()
        assert message.channel == "max"
        assert message.recipient == CHAT_ID
        assert message.payload_reference["text"] == "готово"
    call = app.state.hermes_bridge.calls[0]
    assert call["owner_id"] == OWNER_ID
    assert call["text"] == "собери отчёт"


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


# ── P8.5.7: вложения / двухшаговый диалог доменов ────────────────────────

def _attachment_update(*, mid: str = "mid.att", attachment: dict | None = None,
                       text: str = "") -> dict:
    update = _update(text=text, mid=mid)
    update["message"]["body"]["attachments"] = [
        attachment or {"type": "file", "filename": "report.pdf",
                      "payload": {"url": "https://cdn.max.ru/f/abc"}}
    ]
    return update


def test_webhook_stages_attachment_and_asks_for_domain(app, client):
    with patch("helm_core.api.hooks.download_attachment", return_value=b"file bytes"):
        response = post_hook(client, _attachment_update())

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "attachment_staged"
    assert app.state.hermes_bridge.calls == []
    with app.state.session_factory() as session:
        bind_knowledge_user(session, None)
        pending = session.scalars(select(KnowledgePendingAttachment)).one()
        assert pending.channel == "max"
        assert pending.original_filename == "report.pdf"
        message = session.scalars(select(OutboxMessage)).one()
        assert "report.pdf" in message.payload_reference["text"]
        assert "1. personal" in message.payload_reference["text"]
        assert session.scalars(select(Task)).all() == []


def test_webhook_resolves_pending_domain_and_ingests(app, client):
    with patch("helm_core.api.hooks.download_attachment", return_value=b"file bytes"):
        post_hook(client, _attachment_update())

    response = post_hook(client, _update(text="engineering", mid="mid.domain-reply"))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "attachment_ingested"
    assert app.state.hermes_bridge.calls == []
    with app.state.session_factory() as session:
        bind_knowledge_user(session, None)
        assert session.scalars(select(KnowledgePendingAttachment)).all() == []
        source = session.scalars(select(KnowledgeSource)).one()
        assert source.domain == "engineering"
        assert source.original_filename == "report.pdf"
        assert session.scalars(select(Task)).all() == []


def test_webhook_zapiski_domain_reply_forces_client_restricted(app, client):
    with patch("helm_core.api.hooks.download_attachment", return_value=b"client note"):
        post_hook(client, _attachment_update(mid="mid.att-2"))

    post_hook(client, _update(text="simpas/zapiski", mid="mid.domain-reply-2"))

    with app.state.session_factory() as session:
        bind_knowledge_user(session, None)
        source = session.scalars(select(KnowledgeSource)).one()
        assert source.domain == "simpas/zapiski"
        assert source.sensitivity == "client_restricted"


def test_webhook_invalid_domain_reply_reprompts_without_calling_chief(app, client):
    with patch("helm_core.api.hooks.download_attachment", return_value=b"file bytes"):
        post_hook(client, _attachment_update())

    response = post_hook(client, _update(text="какая погода", mid="mid.invalid-reply"))

    assert response.status_code == 200
    assert response.json()["status"] == "attachment_domain_invalid"
    assert app.state.hermes_bridge.calls == []
    with app.state.session_factory() as session:
        bind_knowledge_user(session, None)
        assert session.scalars(select(KnowledgePendingAttachment)).one() is not None
        assert session.scalars(select(Task)).all() == []


def test_webhook_cancel_removes_pending_attachment(app, client):
    with patch("helm_core.api.hooks.download_attachment", return_value=b"file bytes"):
        post_hook(client, _attachment_update())

    response = post_hook(client, _update(text="отмена", mid="mid.cancel-reply"))

    assert response.status_code == 200
    assert response.json()["status"] == "attachment_cancelled"
    with app.state.session_factory() as session:
        bind_knowledge_user(session, None)
        assert session.scalars(select(KnowledgePendingAttachment)).all() == []
        assert session.scalars(select(KnowledgeSource)).all() == []


def test_webhook_duplicate_attachment_delivery_is_deduped(app, client):
    with patch("helm_core.api.hooks.download_attachment",
              return_value=b"file bytes") as fake_download:
        first = post_hook(client, _attachment_update())
        second = post_hook(client, _attachment_update())

    assert first.json()["status"] == "attachment_staged"
    assert second.json()["status"] == "duplicate"
    assert fake_download.call_count == 1
    with app.state.session_factory() as session:
        bind_knowledge_user(session, None)
        assert len(session.scalars(select(KnowledgePendingAttachment)).all()) == 1


def test_webhook_attachment_download_failure_notifies_owner(app, client):
    with patch("helm_core.api.hooks.download_attachment", side_effect=OSError("boom")):
        response = post_hook(client, _attachment_update())

    assert response.status_code == 200
    assert response.json()["status"] == "attachment_failed"
    with app.state.session_factory() as session:
        bind_knowledge_user(session, None)
        assert session.scalars(select(KnowledgePendingAttachment)).all() == []
        message = session.scalars(select(OutboxMessage)).one()
        assert "скачать" in message.payload_reference["text"]


def test_webhook_unknown_attachment_shape_notifies_owner_instead_of_crashing(app, client):
    bad_attachment = {"type": "file", "payload": {"token": "opaque"}}

    response = post_hook(client, _attachment_update(attachment=bad_attachment))

    assert response.status_code == 200
    assert response.json()["status"] == "attachment_failed"
    with app.state.session_factory() as session:
        bind_knowledge_user(session, None)
        assert session.scalars(select(KnowledgePendingAttachment)).all() == []


# ── v3.7 P8.5.2.1: ZIP batch ingest через /hooks/max ─────────────────────

def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _zip_attachment_update(*, mid: str = "mid.zip", text: str = "") -> dict:
    update = _update(text=text, mid=mid)
    update["message"]["body"]["attachments"] = [
        {"type": "file", "filename": "lectures.zip",
        "payload": {"url": "https://cdn.max.ru/f/zip"}}
    ]
    return update


def test_webhook_zip_attachment_routes_to_batch_not_single_attachment(app, client):
    data = _zip_bytes({"one.txt": b"first", "two.txt": b"second"})
    with patch("helm_core.api.hooks.download_attachment", return_value=data):
        response = post_hook(client, _zip_attachment_update())

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "batch_staged"
    with app.state.session_factory() as session:
        bind_knowledge_user(session, None)
        # Не одиночный диалог — ZIP не попадает в KnowledgePendingAttachment.
        assert session.scalars(select(KnowledgePendingAttachment)).all() == []
        batch = session.scalars(select(KnowledgeIngestBatch)).one()
        assert batch.channel == "max"
        assert batch.total_members == 2
        message = session.scalars(select(OutboxMessage)).one()
        assert "2 файлов" in message.payload_reference["text"]


def test_webhook_zip_domain_reply_queues_children_without_calling_chief(app, client):
    data = _zip_bytes({"one.txt": b"first"})
    with patch("helm_core.api.hooks.download_attachment", return_value=data):
        post_hook(client, _zip_attachment_update())

    response = post_hook(client, _update(text="engineering", mid="mid.zip-domain"))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "batch_queued"
    assert app.state.hermes_bridge.calls == []
    with app.state.session_factory() as session:
        bind_knowledge_user(session, None)
        item = session.scalars(select(KnowledgeBatchItem)).one()
        source = session.get(KnowledgeSource, item.source_id)
        assert source.domain == "engineering"
        assert session.scalars(select(Task)).all() == []


# ── P8.5.12: Micro-Memory «Запомни» через /hooks/max ────────────────────

def test_webhook_remembers_fact_and_confirms_without_calling_chief(app, client):
    response = post_hook(client, _update(text="Запомни номер машины курьера: А123ВС77",
                                         mid="mid.remember-1"))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "remember_stored"
    assert app.state.hermes_bridge.calls == []
    with app.state.session_factory() as session:
        from helm_core.models import KnowledgeMemory, Task
        bind_knowledge_user(session, None)
        memory = session.scalars(select(KnowledgeMemory)).one()
        assert "А123ВС77" in memory.canonical_text
        message = session.scalars(select(OutboxMessage)).one()
        assert "А123ВС77" in message.payload_reference["text"]
        assert session.scalars(select(Task)).all() == []


def test_webhook_remember_rejects_forbidden_secret(app, client):
    from helm_core.knowledge.memory import FORBIDDEN_SECRET_NOTICE

    response = post_hook(client, _update(text="Запомни пароль от почты: hunter2",
                                         mid="mid.remember-secret"))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "remember_rejected_secret"
    with app.state.session_factory() as session:
        from helm_core.models import KnowledgeMemory
        bind_knowledge_user(session, None)
        assert session.scalars(select(KnowledgeMemory)).all() == []
        message = session.scalars(select(OutboxMessage)).one()
        assert message.payload_reference["text"] == FORBIDDEN_SECRET_NOTICE


def test_webhook_remember_takes_priority_over_pending_domain_reply(app, client):
    """"Запомни ..." не должно попасть в resolve_pending_domain() как
    "неверный ответ на вопрос о домене" — приоритет отдан Remember."""
    with patch("helm_core.api.hooks.download_attachment", return_value=b"file bytes"):
        post_hook(client, _attachment_update(mid="mid.att-remember"))

    response = post_hook(client, _update(text="Запомни купить молоко",
                                         mid="mid.remember-during-pending"))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "remember_stored"
    with app.state.session_factory() as session:
        from helm_core.models import KnowledgePendingAttachment
        bind_knowledge_user(session, None)
        # Вложение остаётся ожидать домен — Remember его не тронул.
        assert session.scalars(select(KnowledgePendingAttachment)).one() is not None


def test_webhook_answers_locally_without_calling_chief_when_probe_finds_answer(app, client):
    """§14.11: LOCAL_ANSWER отвечает напрямую — Hermes вообще не вызывается."""
    from helm_core.knowledge.ingest import ingest_text

    with app.state.session_factory() as session:
        ingest_text(session, domain="engineering", text="Решение: используем Postgres.")
        session.commit()

    response = post_hook(client, _update(text="какое решение приняли"))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "local_answer"
    assert app.state.hermes_bridge.calls == []
    with app.state.session_factory() as session:
        message = session.scalars(select(OutboxMessage)).one()
        assert message.channel == "max"
        assert "Postgres" in message.payload_reference["text"]


def test_webhook_calls_chief_when_probe_finds_nothing(app, client):
    """Пустой корпус — NEEDS_REASONING, обычный путь через Hermes не меняется."""
    response = post_hook(client, _update(text="собери отчёт"))

    assert response.status_code == 202
    assert len(app.state.hermes_bridge.calls) == 1
    with app.state.session_factory() as session:
        from helm_core.models import KnowledgeAnswerRun
        bind_knowledge_user(session, None)
        run = session.scalars(select(KnowledgeAnswerRun)).one()
        assert run.mode == "C1"
        assert run.paid_ai_used is True


def test_webhook_queues_transport_notice_when_chief_is_down(app, client, caplog):
    """§10.3: task остаётся REGISTERED, владелец получает транспортное уведомление.

    Обработчик вебхука отвечает 'accepted' ВСЕГДА и сразу: вызов Hermes
    уходит в фоновую задачу (он может идти минуты), и различие
    успех/недоступность видно только по результату в outbox, не в ответе
    на сам вебхук.
    """
    app.state.hermes_bridge = FakeBridge(available=False)

    with caplog.at_level("WARNING"):
        response = post_hook(client, _update())

    # НАЙДЕНО 29.08.2026: раньше причина падения писалась только в
    # TaskEvent (БД) — docker compose logs не показывал вообще ничего,
    # диагностика требовала прямого psql-запроса. Тип исключения
    # обязан попадать в лог.
    assert "HermesUnavailable" in caplog.text

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
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


def test_deliver_pending_logs_failure_type_without_message_text(session, caplog):
    """Диагностика нужна (иначе отказ канала неотличим от бага формата),
    но текст сообщения владельца в лог попадать не должен ни при каких
    обстоятельствах — исключения провайдеров могут содержать эхо запроса.
    """
    secret_text = "выдай мне пароль от всего"
    _queue(session, text=secret_text)

    class FailingSender:
        def __call__(self, recipient: str, text: str) -> None:
            raise RuntimeError(f"echo: {text}")

    with caplog.at_level("WARNING"):
        deliver_pending(session, {"max": FailingSender()})

    assert secret_text not in caplog.text
    assert "RuntimeError" in caplog.text
    assert "max" in caplog.text


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
