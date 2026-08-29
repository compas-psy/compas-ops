"""API Control Plane: HMAC, гейт §9.3 и требования §30.7 к заголовкам."""

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from helm_core.api.security import sign
from helm_core.app import create_app
from helm_core.config import Settings
from helm_core.models import Base, Task

from conftest import DB_URL, OWNER_ID, POLICY_PATH

SERVICE_SECRET = "test-service-secret"


@pytest.fixture
def client(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    settings = Settings(database_url=DB_URL, policy_path=POLICY_PATH, owner_id=OWNER_ID)
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
