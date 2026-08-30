"""API Control Plane: HMAC, гейт §9.3 и требования §30.7 к заголовкам."""

import base64
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from helm_core.api.security import sign
from helm_core.app import create_app
from helm_core.config import Settings
from helm_core.knowledge.rls import apply_rls
from helm_core.models import Base, Task

from conftest import DB_URL, OWNER_ID, POLICY_PATH, seed_system_owner

SERVICE_SECRET = "test-service-secret"


@pytest.fixture
def client(engine, tmp_path, monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_rls(conn)
    seed_system_owner(engine)
    settings = Settings(database_url=DB_URL, policy_path=POLICY_PATH, owner_id=OWNER_ID)
    # /internal/knowledge/attachment/* (P8.5.7, Telegram) зовёт stage_
    # attachment()/resolve_pending_domain() без spool_root/vault_root —
    # без monkeypatch тесты писали бы в реальные /opt/helm-state/
    # knowledge-spool и /opt/helm-knowledge на этой машине (тот же класс
    # утечки, что уже нашёлся и починен в test_max_channel.py::app).
    import helm_core.api.internal as internal_module
    spool_root = str(tmp_path / "spool")
    vault_root = str(tmp_path / "vault")
    real_stage = internal_module.stage_attachment
    real_resolve = internal_module.resolve_pending_domain
    monkeypatch.setattr(internal_module, "stage_attachment",
                        lambda *a, **kw: real_stage(*a, spool_root=spool_root, **kw))
    monkeypatch.setattr(internal_module, "resolve_pending_domain",
                        lambda *a, **kw: real_resolve(*a, vault_root=vault_root, **kw))
    # То же самое для ZIP-batch (P8.5.2.1) — raw_batches_root/vault_root
    # по умолчанию тоже /opt/helm-knowledge/..., та же утечка иначе.
    raw_batches_root = str(tmp_path / "raw-batches")
    real_stage_batch = internal_module.stage_batch
    real_resolve_batch = internal_module.resolve_batch_domain
    monkeypatch.setattr(internal_module, "stage_batch",
                        lambda *a, **kw: real_stage_batch(*a, raw_batches_root=raw_batches_root, **kw))
    monkeypatch.setattr(internal_module, "resolve_batch_domain",
                        lambda *a, **kw: real_resolve_batch(*a, vault_root=vault_root, **kw))
    # P8.5.12: та же утечка — try_remember() пишет Markdown-зеркало под
    # DEFAULT_VAULT_ROOT по умолчанию.
    real_try_remember = internal_module.try_remember
    monkeypatch.setattr(internal_module, "try_remember",
                        lambda *a, **kw: real_try_remember(*a, vault_root=vault_root, **kw))
    return TestClient(create_app(settings, service_secret=SERVICE_SECRET))


def signed(path: str, body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    return {"X-Helm-Timestamp": ts, "X-Helm-Signature": sign(SERVICE_SECRET, ts, body),
            "Content-Type": "application/json"}


def post_internal(client, path: str, payload: dict):
    import json
    body = json.dumps(payload).encode()
    return client.post(path, content=body, headers=signed(path, body))


# ── §9.3 / A-DoD п.2: регистрация до LLM ────────────────────────────────────

def test_inbound_registers_task(client):
    r = post_internal(client, "/internal/inbound", {
        "channel": "telegram", "external_message_id": "m1",
        "owner_id": OWNER_ID, "text": "собери отчёт",
    })
    assert r.status_code == 201, r.text
    assert r.json()["created"] is True
    assert r.json()["status"] == "REGISTERED"


def test_inbound_rejects_non_owner(client):
    r = post_internal(client, "/internal/inbound", {
        "channel": "telegram", "external_message_id": "m2",
        "owner_id": "tg:999", "text": "сделай что-нибудь",
    })
    assert r.status_code == 403


# ── §14.11: Knowledge Probe как internal endpoint, вызываемый до Hermes ──────

def test_knowledge_probe_endpoint_returns_needs_reasoning_on_empty_corpus(client):
    r = post_internal(client, "/internal/knowledge/probe", {"query": "что угодно"})
    assert r.status_code == 200, r.text
    assert r.json() == {"outcome": "NEEDS_REASONING", "mode": None, "answer_text": None}


def test_knowledge_probe_endpoint_returns_local_answer(client):
    from helm_core.knowledge.ingest import ingest_text

    with client.app.state.session_factory() as session:
        ingest_text(session, domain="engineering", text="Решение: используем Postgres.")
        session.commit()

    r = post_internal(client, "/internal/knowledge/probe", {"query": "какое решение приняли"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "LOCAL_ANSWER"
    assert body["mode"] == "Z0"
    assert "Postgres" in body["answer_text"]


def test_knowledge_probe_endpoint_requires_service_auth(client):
    r = client.post("/internal/knowledge/probe", json={"query": "что угодно"})
    assert r.status_code == 422 or r.status_code == 401


# ── P8.5.12 Telegram-сторона: /internal/knowledge/remember ───────────────────

def test_knowledge_remember_endpoint_stores_and_confirms(client):
    r = post_internal(client, "/internal/knowledge/remember", {
        "channel": "telegram", "text": "Запомни номер машины курьера: А123ВС77",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "stored"
    assert "А123ВС77" in body["text"]

    with client.app.state.session_factory() as session:
        from helm_core.knowledge.tenancy import bind_knowledge_user
        from helm_core.models import KnowledgeMemory
        bind_knowledge_user(session, None)
        memory = session.scalars(select(KnowledgeMemory)).one()
        assert "А123ВС77" in memory.canonical_text


def test_knowledge_remember_endpoint_not_a_command(client):
    r = post_internal(client, "/internal/knowledge/remember", {
        "channel": "telegram", "text": "какое решение приняли по проекту",
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "not_command", "text": None}


def test_knowledge_remember_endpoint_rejects_forbidden_secret(client):
    r = post_internal(client, "/internal/knowledge/remember", {
        "channel": "telegram", "text": "Запомни пароль от почты: hunter2",
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected_secret"


def test_knowledge_remember_endpoint_requires_service_auth(client):
    r = client.post("/internal/knowledge/remember",
                    json={"channel": "telegram", "text": "Запомни что угодно"})
    assert r.status_code == 422 or r.status_code == 401


# ── P8.6.2 Telegram-сторона: /internal/knowledge/users/invite ────────────────

def test_knowledge_users_invite_endpoint_creates_invited_user(client):
    r = post_internal(client, "/internal/knowledge/users/invite", {
        "display_name": "Тестовый пользователь",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert uuid.UUID(body["knowledge_user_id"])
    assert len(body["invite_token"]) > 20
    assert body["expires_at"]

    with client.app.state.session_factory() as session:
        from helm_core.models import KnowledgeUser, KnowledgeUserStatus
        user = session.get(KnowledgeUser, uuid.UUID(body["knowledge_user_id"]))
        assert user.status == KnowledgeUserStatus.INVITED
        assert user.display_name == "Тестовый пользователь"


def test_knowledge_users_invite_endpoint_builds_deep_link_when_bot_username_configured(client):
    client.app.state.settings.knowledge_telegram_bot_username = "my_knowledge_bot"
    r = post_internal(client, "/internal/knowledge/users/invite", {})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["deep_link"] == f"https://t.me/my_knowledge_bot?start=kb_{body['invite_token']}"


def test_knowledge_users_invite_endpoint_requires_service_auth(client):
    r = client.post("/internal/knowledge/users/invite", json={})
    assert r.status_code == 422 or r.status_code == 401


def test_knowledge_users_suspend_and_reactivate_endpoints(client):
    invite = post_internal(client, "/internal/knowledge/users/invite", {}).json()
    user_id = invite["knowledge_user_id"]

    suspend = post_internal(client, f"/internal/knowledge/users/{user_id}/suspend", {})
    assert suspend.status_code == 200, suspend.text
    assert suspend.json()["status"] == "success"

    reactivate = post_internal(client, f"/internal/knowledge/users/{user_id}/reactivate", {})
    assert reactivate.status_code == 200, reactivate.text
    assert reactivate.json()["status"] == "success"

    with client.app.state.session_factory() as session:
        from helm_core.models import KnowledgeUser, KnowledgeUserStatus
        user = session.get(KnowledgeUser, uuid.UUID(user_id))
        assert user.status == KnowledgeUserStatus.ACTIVE


def test_knowledge_users_suspend_endpoint_404_for_unknown_user(client):
    r = post_internal(client, f"/internal/knowledge/users/{uuid.uuid4()}/suspend", {})
    assert r.status_code == 404


# ── P8.5.7 Telegram-сторона: /internal/knowledge/attachment/* ────────────────
# helm-control работает вне процесса Control Plane (хост Hermes, свой venv)
# и не может звать chat_intake.py напрямую — те же функции, что MAX вызывает
# in-process в /hooks/max, здесь доступны по HMAC-подписанному HTTP.

def test_attachment_stage_endpoint_returns_domain_menu(client):
    r = post_internal(client, "/internal/knowledge/attachment/stage", {
        "channel": "telegram",
        "data_base64": base64.b64encode(b"file bytes").decode(),
        "original_filename": "report.pdf",
        "mime_type": "application/pdf",
    })

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "staged"
    assert "report.pdf" in body["text"]
    assert "1. personal" in body["text"]


def test_attachment_stage_endpoint_rejects_oversized_file(client):
    from helm_core.knowledge.chat_intake import MAX_ATTACHMENT_BYTES

    r = post_internal(client, "/internal/knowledge/attachment/stage", {
        "channel": "telegram",
        "data_base64": base64.b64encode(b"x" * (MAX_ATTACHMENT_BYTES + 1)).decode(),
        "original_filename": "huge.bin",
    })

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "too_large"


def test_attachment_stage_endpoint_rejects_bad_base64(client):
    r = post_internal(client, "/internal/knowledge/attachment/stage", {
        "channel": "telegram", "data_base64": "not-valid-base64!!!",
    })
    assert r.status_code == 400


def test_attachment_resolve_endpoint_not_pending_when_nothing_staged(client):
    r = post_internal(client, "/internal/knowledge/attachment/resolve", {
        "channel": "telegram", "reply_text": "engineering",
    })

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "not_pending"
    assert body["text"] is None


def test_attachment_resolve_endpoint_ingests_by_domain_alias(client):
    post_internal(client, "/internal/knowledge/attachment/stage", {
        "channel": "telegram",
        "data_base64": base64.b64encode(b"file bytes").decode(),
        "original_filename": "report.pdf",
    })

    r = post_internal(client, "/internal/knowledge/attachment/resolve", {
        "channel": "telegram", "reply_text": "company", "recipient": "12345",
    })

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ingested"
    assert "simpas/company" in body["text"]
    with client.app.state.session_factory() as session:
        from sqlalchemy import select
        from helm_core.knowledge.tenancy import bind_knowledge_user
        from helm_core.models import KnowledgeSource
        bind_knowledge_user(session, None)
        source = session.scalars(select(KnowledgeSource)).one()
        assert source.domain == "simpas/company"


def test_attachment_resolve_endpoint_requires_service_auth(client):
    r = client.post("/internal/knowledge/attachment/resolve",
                    json={"channel": "telegram", "reply_text": "engineering"})
    assert r.status_code == 422 or r.status_code == 401


# ── v3.7 P8.5.2.1: ZIP batch ingest internal endpoints ─────────────────────

def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_batches_stage_endpoint_returns_domain_menu(client):
    r = post_internal(client, "/internal/knowledge/batches", {
        "channel": "telegram",
        "data_base64": base64.b64encode(_zip_bytes({"a.txt": b"one", "b.txt": b"two"})).decode(),
        "original_filename": "lectures.zip",
        "mime_type": "application/zip",
    })

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "staged"
    assert "2 файлов" in body["text"]
    assert "1. personal" in body["text"]


def test_batches_stage_endpoint_blocks_encrypted_archive(client):
    data = bytearray(_zip_bytes({"secret.txt": b"x"}))
    data[data.index(b"PK\x03\x04") + 6] |= 0x1
    data[data.index(b"PK\x01\x02") + 8] |= 0x1

    r = post_internal(client, "/internal/knowledge/batches", {
        "channel": "telegram", "data_base64": base64.b64encode(bytes(data)).decode(),
        "original_filename": "enc.zip", "mime_type": "application/zip",
    })

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "blocked"
    assert "не принят" in body["text"]


def test_batches_resolve_domain_endpoint_queues_and_notifies_on_completion(client):
    post_internal(client, "/internal/knowledge/batches", {
        "channel": "telegram",
        "data_base64": base64.b64encode(_zip_bytes({"a.txt": b"one"})).decode(),
        "original_filename": "a.zip", "mime_type": "application/zip",
        "recipient": "555",
    })

    r = post_internal(client, "/internal/knowledge/batches/resolve-domain", {
        "channel": "telegram", "reply_text": "engineering",
    })

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert "1 " in body["text"] or "Поставил в очередь" in body["text"]

    with client.app.state.session_factory() as session:
        from sqlalchemy import select
        from helm_core.knowledge.tenancy import bind_knowledge_user
        from helm_core.models import KnowledgeBatchItem, KnowledgeSource
        bind_knowledge_user(session, None)
        item = session.scalars(select(KnowledgeBatchItem)).one()
        source = session.get(KnowledgeSource, item.source_id)
        assert source.domain == "engineering"


def test_batches_resolve_domain_not_pending_when_nothing_staged(client):
    r = post_internal(client, "/internal/knowledge/batches/resolve-domain", {
        "channel": "telegram", "reply_text": "engineering",
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "not_pending"


# ── A-DoD п.4-6: propose()/decision() через реальный HTTP, не сервис напрямую ──

def test_propose_green_executes_immediately_via_http(client):
    r = post_internal(client, "/internal/actions/propose", {
        "action_type": "notify_owner", "payload": {"text": "готово"},
    })
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "EXECUTED"


def test_propose_red_stays_pending_via_http(client):
    r = post_internal(client, "/internal/actions/propose", {
        "action_type": "publish_public_content",
        "payload": {"channel": "tg_test", "body": "текст"},
    })
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "PENDING"


def test_decision_precondition_failure_is_409_not_500(client):
    """НАЙДЕНО на живом смоук-тесте: PreconditionFailed не ловился этим
    роутом и улетал как голый 500 вместо осмысленной ошибки.
    """
    approval_id = post_internal(client, "/internal/actions/propose", {
        "action_type": "publish_public_content",
        "payload": {"channel": "tg_test", "body": "текст"},
    }).json()["approval_id"]

    r = post_internal(client, f"/internal/approvals/{approval_id}/decision", {
        "approve": True, "decided_by": OWNER_ID, "channel": "telegram",
    })
    assert r.status_code == 409, r.text


# ── service auth ────────────────────────────────────────────────────────────

def test_internal_requires_signature(client):
    r = client.post("/internal/inbound", json={
        "channel": "telegram", "external_message_id": "m3",
        "owner_id": OWNER_ID, "text": "x",
    })
    assert r.status_code == 422 or r.status_code == 401


def test_internal_rejects_wrong_signature(client):
    import json
    body = json.dumps({"channel": "telegram", "external_message_id": "m4",
                       "owner_id": OWNER_ID, "text": "x"}).encode()
    ts = str(int(time.time()))
    r = client.post("/internal/inbound", content=body, headers={
        "X-Helm-Timestamp": ts, "X-Helm-Signature": "0" * 64,
        "Content-Type": "application/json"})
    assert r.status_code == 401


def test_internal_rejects_stale_timestamp(client):
    """Метка времени внутри подписи не даёт переиграть старый запрос."""
    import json
    body = json.dumps({"channel": "telegram", "external_message_id": "m5",
                       "owner_id": OWNER_ID, "text": "x"}).encode()
    ts = str(int(time.time()) - 3600)
    r = client.post("/internal/inbound", content=body, headers={
        "X-Helm-Timestamp": ts, "X-Helm-Signature": sign(SERVICE_SECRET, ts, body),
        "Content-Type": "application/json"})
    assert r.status_code == 401


def test_signature_covers_body(client):
    """Подпись покрывает тело: подменить payload при валидной подписи нельзя."""
    import json
    original = json.dumps({"channel": "telegram", "external_message_id": "m6",
                           "owner_id": OWNER_ID, "text": "безобидно"}).encode()
    tampered = json.dumps({"channel": "telegram", "external_message_id": "m6",
                           "owner_id": OWNER_ID, "text": "ПОДМЕНА"}).encode()
    ts = str(int(time.time()))
    r = client.post("/internal/inbound", content=tampered, headers={
        "X-Helm-Timestamp": ts, "X-Helm-Signature": sign(SERVICE_SECRET, ts, original),
        "Content-Type": "application/json"})
    assert r.status_code == 401


# ── §30.7: панель ───────────────────────────────────────────────────────────

def test_panel_get_requires_session(client):
    for path in ("/today", "/approvals", "/tasks", "/money", "/system"):
        r = client.get(f"/api/panel/v1{path}")
        assert r.status_code == 401, f"{path} отдался без сессии"


def test_panel_write_requires_session(client):
    r = client.post(f"/api/panel/v1/actions/{uuid.uuid4()}/approve")
    assert r.status_code == 401


def test_security_headers_present(client):
    """§30.7: no-store на API, CSP/frame/referrer проходят проверку."""
    r = client.get("/api/panel/v1/today")
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    csp = r.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "default-src 'self'" in csp


#: Всё, через что панель могла бы дотянуться до модели или до сети.
#: httpx/requests тоже здесь: сетевой клиент в модуле панели — это способ
#: позвать Hermes «в обход», даже если имя модели нигде не упомянуто.
FORBIDDEN_IN_PANEL = {
    "litellm", "openai", "anthropic", "openrouter", "httpx", "requests",
    "urllib", "urllib3", "aiohttp", "socket", "subprocess",
}


def _imported_modules(path: str) -> set[str]:
    """Корневые имена всех модулей, импортируемых файлом."""
    import ast

    roots: set[str] = set()
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_panel_module_cannot_call_a_model(client):
    """§30.7 «read GET never triggers Hermes/LiteLLM».

    Проверяется структурно, а не наблюдением: модуль panel.py не импортирует
    ни клиента модели, ни сетевого клиента, поэтому позвать модель из GET
    нельзя, не переписав файл. Разбор идёт по AST, а не поиском подстроки:
    упоминание LiteLLM в комментарии — не вызов, а вот `import httpx` — да.
    """
    import helm_core.api.panel as panel_module

    leaked = _imported_modules(panel_module.__file__) & FORBIDDEN_IN_PANEL
    assert not leaked, f"panel.py импортирует {sorted(leaked)} — GET может разбудить модель"


def test_forbidden_import_would_be_caught(tmp_path):
    """Проверка самой проверки: подсаженный импорт обязан быть найден."""
    probe = tmp_path / "probe.py"
    probe.write_text("import httpx\nfrom helm_core.models import Task\n", encoding="utf-8")
    assert _imported_modules(str(probe)) & FORBIDDEN_IN_PANEL == {"httpx"}


def test_openapi_is_not_exposed(client):
    """Схема internal API не публикуется: §4.6 запрещает публичный admin API."""
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
