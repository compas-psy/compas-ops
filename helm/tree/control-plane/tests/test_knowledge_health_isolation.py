"""ADR-005/P12 — generic public source envelope + security-scope private
payload schema (health).

Область этих тестов — маршрутизация ПРИЛОЖЕНИЯ: правильные данные лежат
в правильной таблице/схеме, `public` не видит того, чему запрещено, поиск
ходит куда нужно. Здесь НЕТ второй настоящей Postgres-роли (`helm_health`
поверх `helm_app`) — health-таблицы созданы под той же `helm_rls`, что и
`public` (см. `tests/README.md`: понижение bootstrap-роли и заведение
второй роли требует ручного шага, не автоматизируется тестовым запуском).
Поэтому то, что здесь проверяется: RLS-тенантность (`helm_rls` не
суперпользователь и не BYPASSRLS — политики реально применяются) и
корректность маршрутизации кода. То, что здесь НЕ проверяется:
физический REVOKE между `helm_app` и `helm_health` (acceptance #3/#4
владельца) — это проверяет собственный verification-блок `scripts/
setup-health-role.sh` на живом сервере.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
import sqlalchemy.exc
from sqlalchemy import func, select, text

from helm_core.config import get_settings
from helm_core.knowledge import atomizer
from helm_core.knowledge import health_schema
from helm_core.knowledge import worker as worker_module
from helm_core.knowledge.atomizer import AtomizedAtom, store_notes
from helm_core.knowledge.vault import scope_root
from helm_core.knowledge.documents import find_sources, read_original
from helm_core.knowledge.offboarding import delete_user_permanently
from helm_core.knowledge import ingest as ingest_module
from helm_core.knowledge.ingest import ingest_text, register_file_for_ingest
from helm_core.knowledge.probe import probe
from helm_core.knowledge.semantic_extract import ExtractedAtom, ExtractedEntity, WindowExtraction
from helm_core.knowledge.semantic_publish import publish_semantic_run
from helm_core.knowledge.worker import process_job
from helm_core.models import (
    HealthKnowledgeChunk, HealthKnowledgeEdge, HealthKnowledgeEntityAlias,
    HealthKnowledgeNode, HealthKnowledgeNodeMention,
    HealthKnowledgeNote, HealthKnowledgeRelation, HealthKnowledgeSourcePrivate,
    KnowledgeChunk, KnowledgeIngestStatus, KnowledgeNote, KnowledgeRelation, KnowledgeSource,
    KnowledgeUser, KnowledgeUserRole, KnowledgeUserStatus, OutboxMessage, SemanticRunStatus,
)
from helm_core.models.health_tables import HealthBase
from conftest import DB_URL

HEALTH_TABLES = ("knowledge_relations", "knowledge_chunks", "knowledge_source_private",
                 "knowledge_notes", "knowledge_nodes", "knowledge_node_mentions",
                 "knowledge_edges", "knowledge_entity_aliases")


@pytest.fixture(scope="session", autouse=True)
def _health_schema_ddl(engine):
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS health CASCADE"))
        conn.execute(text("CREATE SCHEMA health"))
    HealthBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for table in HEALTH_TABLES:
            conn.execute(text(f"ALTER TABLE health.{table} ENABLE ROW LEVEL SECURITY"))
            conn.execute(text(f"ALTER TABLE health.{table} FORCE ROW LEVEL SECURITY"))
            conn.execute(text(f"DROP POLICY IF EXISTS knowledge_tenant_isolation ON health.{table}"))
            conn.execute(text(
                f"CREATE POLICY knowledge_tenant_isolation ON health.{table} "
                "USING (knowledge_user_id = NULLIF(current_setting("
                "'app.current_knowledge_user_id', true), '')::uuid) "
                "WITH CHECK (knowledge_user_id = NULLIF(current_setting("
                "'app.current_knowledge_user_id', true), '')::uuid)"
            ))


@pytest.fixture(autouse=True)
def _clean_health_tables(engine):
    yield
    with engine.begin() as conn:
        # knowledge_nodes перечислена ЯВНО: внешнего ключа на сайдкар у
        # неё нет, и CASCADE от него до неё не доходит — узлы пережили бы
        # чистку и попали в следующий тест.
        conn.execute(text(
            "TRUNCATE health.knowledge_relations, health.knowledge_chunks, "
            "health.knowledge_notes, health.knowledge_nodes, "
            "health.knowledge_node_mentions, health.knowledge_edges, "
            "health.knowledge_entity_aliases, health.knowledge_source_private "
            "RESTART IDENTITY CASCADE"
        ))


@pytest.fixture
def health_configured(monkeypatch):
    """Включает health-путь на один тест. `health_database_url` указывает
    на ТУ ЖЕ тестовую БД/роль, что и public — см. docstring модуля."""
    monkeypatch.setenv("HELM_HEALTH_DATABASE_URL", DB_URL)
    get_settings.cache_clear()
    health_schema._health_engine_or_none.cache_clear()
    yield
    get_settings.cache_clear()
    health_schema._health_engine_or_none.cache_clear()


@pytest.fixture
def user(session):
    u = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(u)
    session.flush()
    return u


# ── health_schema.py — прямые unit-тесты ──────────────────────────────────

def test_health_schema_configured_reflects_settings(health_configured):
    assert health_schema.health_schema_configured() is True


def test_health_schema_not_configured_by_default():
    assert health_schema.health_schema_configured() is False


def test_write_read_original_filename_roundtrip(health_configured, user):
    source_id = uuid.uuid4()
    health_schema.write_original_filename(
        source_id=source_id, knowledge_user_id=user.id,
        original_filename="Консультация уролога.pdf")

    assert health_schema.read_original_filename(
        source_id=source_id, knowledge_user_id=user.id) == "Консультация уролога.pdf"


def test_read_original_filename_is_tenant_isolated(health_configured, session):
    other = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(other)
    session.flush()
    source_id = uuid.uuid4()
    health_schema.write_original_filename(
        source_id=source_id, knowledge_user_id=other.id, original_filename="ВИЧ-анализ.pdf")

    assert health_schema.read_original_filename(
        source_id=source_id, knowledge_user_id=uuid.uuid4()) is None


def test_record_parse_error_writes_full_diagnostic(health_configured, user):
    source_id = uuid.uuid4()
    health_schema.write_original_filename(
        source_id=source_id, knowledge_user_id=user.id, original_filename="анализ.pdf")

    health_schema.record_parse_error(
        source_id=source_id, knowledge_user_id=user.id,
        message="ValueError: страница 3 повреждена, обнаружен B12")

    with health_schema.health_session(user.id) as hs:
        row = hs.get(HealthKnowledgeSourcePrivate, source_id)
        assert row.parse_error == "ValueError: страница 3 повреждена, обнаружен B12"


def test_write_relations_is_idempotent_on_reingest(health_configured, user):
    source_id = uuid.uuid4()
    # HealthKnowledgeRelation.source_id — настоящий FK на
    # HealthKnowledgeSourcePrivate (обе таблицы в одной схеме, одна роль
    # владеет обеими) — родительская строка нужна первой, тот же порядок,
    # что в реальном pipeline (write_original_filename() всегда раньше
    # write_chunks()/write_relations(), см. ingest.py).
    health_schema.write_original_filename(source_id=source_id, knowledge_user_id=user.id,
                                          original_filename=None)
    health_schema.write_relations(source_id=source_id, knowledge_user_id=user.id, from_id="A",
                                  relations=[("B", "relates_to", "explicit_link"),
                                            ("C", "relates_to", "explicit_link")])
    health_schema.write_relations(source_id=source_id, knowledge_user_id=user.id, from_id="A",
                                  relations=[("B", "relates_to", "explicit_link")])

    with health_schema.health_session(user.id) as hs:
        rows = hs.scalars(
            select(HealthKnowledgeRelation).where(HealthKnowledgeRelation.source_id == source_id)
        ).all()
    assert {r.to_id for r in rows} == {"B"}


# ── ingest.py — маршрутизация ─────────────────────────────────────────────

def test_ingest_text_health_domain_fallback_when_not_configured(session, user):
    """Без прогона scripts/setup-health-role.sh — прежнее поведение,
    ничего не падает и не теряется."""
    source = ingest_text(session, domain="health", text="Анализ крови показал дефицит железа.",
                         original_filename="Анализ крови.pdf", knowledge_user_id=user.id)
    session.flush()

    assert source.original_filename == "Анализ крови.pdf"
    chunks = session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id)).all()
    assert [c.text for c in chunks] == ["Анализ крови показал дефицит железа."]


def test_ingest_text_health_domain_moves_filename_and_chunks_to_sidecar(
        session, health_configured, user):
    source = ingest_text(session, domain="health", text="Анализ крови показал дефицит железа.",
                         original_filename="Анализ крови.pdf", knowledge_user_id=user.id)
    session.flush()

    assert source.original_filename is None
    assert health_schema.read_original_filename(
        source_id=source.id, knowledge_user_id=user.id) == "Анализ крови.pdf"
    assert session.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id)).all() == []
    with health_schema.health_session(user.id) as hs:
        chunks = hs.scalars(
            select(HealthKnowledgeChunk).where(HealthKnowledgeChunk.source_id == source.id)).all()
        assert [c.text for c in chunks] == ["Анализ крови показал дефицит железа."]


def test_ingest_text_health_domain_routes_relations_to_sidecar(session, health_configured, user):
    source = ingest_text(session, domain="health", text="Смотри [[Анализ на ВИЧ]].",
                         knowledge_user_id=user.id)
    session.flush()

    assert session.query(KnowledgeRelation).filter(
        KnowledgeRelation.source_id == source.id).count() == 0
    with health_schema.health_session(user.id) as hs:
        rows = hs.scalars(
            select(HealthKnowledgeRelation).where(HealthKnowledgeRelation.source_id == source.id)
        ).all()
        assert [r.to_id for r in rows] == ["Анализ на ВИЧ"]


def test_register_file_for_ingest_health_domain_moves_filename(
        session, tmp_path, health_configured, user):
    raw = tmp_path / "raw.bin"
    raw.write_text("контент", encoding="utf-8")

    result = register_file_for_ingest(
        session, domain="health", raw_path=raw, original_filename="Консультация уролога.pdf",
        knowledge_user_id=user.id)
    session.flush()

    assert result.source.original_filename is None
    assert health_schema.read_original_filename(
        source_id=result.source.id, knowledge_user_id=user.id) == "Консультация уролога.pdf"


# ── atomizer.py (ADR-019) — та же маршрутизация, что chunks/relations ────

def test_health_notes_are_routed_to_the_sidecar(
        session, tmp_path, health_configured, user):
    """Атомизатор не содержит ветки "если health" — маршрутизация приходит
    из is_health_domain()/health_schema_configured(), как у chunks/
    relations выше. Здесь проверяется РЕЗУЛЬТАТ этой же маршрутизации для
    knowledge_notes, не новая логика.

    Вызывается `store_notes()` напрямую, а не через `ingest_text()`:
    точка входа `atomize_and_store()` на время rescue заморожена (R2,
    §30 «legacy semantic-v1 remains read-only»), а маршрутизация записи
    — свойство приватности, а не семантики v1, и переживать заморозку
    она обязана. Тест держит именно её.
    """
    source = ingest_text(session, domain="health", text="Консультация уролога Иванова.",
                         knowledge_user_id=user.id, vault_root=str(tmp_path))
    session.flush()
    root = scope_root(str(tmp_path), domain="health", knowledge_user_id=user.id)
    store_notes(session, domain="health", knowledge_user_id=user.id, source_id=source.id,
                source_sha256=source.sha256, vault_root=root,
                atoms=[AtomizedAtom(slug="Иванов, уролог", type="PERSON",
                                    text="Приём у уролога Иванова.", links=())])
    session.flush()

    assert session.query(KnowledgeNote).filter(
        KnowledgeNote.slug == "Иванов, уролог").count() == 0
    with health_schema.health_session(user.id) as hs:
        notes = hs.scalars(
            select(HealthKnowledgeNote).where(HealthKnowledgeNote.slug == "Иванов, уролог")
        ).all()
        assert len(notes) == 1
        assert notes[0].source_ids == [str(source.id)]

    # До 02.09.2026 здесь ожидался общий `<vault>/entities/` — это и был
    # F15: строка уходила в health-схему, а файл ложился в общее дерево.
    private = (tmp_path.parent / f"{tmp_path.name}-private" / "health" / "users" / str(user.id)
               / "entities" / "Иванов, уролог.md")
    assert "Приём у уролога Иванова." in private.read_text(encoding="utf-8")


# ── worker.py — process_job() health-ветка ────────────────────────────────

@dataclass
class _FakeParseResult:
    text: str
    parser: str
    quality_ok: bool


def _make_health_pending_job(session, tmp_path, name, *, knowledge_user_id, channel=None,
                             recipient=None):
    raw = tmp_path / name
    raw.write_text(f"содержимое {name}", encoding="utf-8")
    result = register_file_for_ingest(
        session, domain="health", raw_path=raw, original_filename=name, vault_root=str(tmp_path),
        knowledge_user_id=knowledge_user_id, channel=channel, recipient=recipient)
    session.flush()
    return result.job


def test_process_job_health_domain_routes_chunks_and_relations_to_sidecar(
        session, tmp_path, monkeypatch, health_configured, user):
    job = _make_health_pending_job(session, tmp_path, "Заключение психиатра.pdf",
                                   knowledge_user_id=user.id)
    monkeypatch.setattr(worker_module, "parse_file",
                        lambda path: _FakeParseResult(
                            text="Диагноз: [[тревожное расстройство]].",
                            parser="markitdown", quality_ok=True))

    process_job(session, job)
    session.flush()

    assert job.status == KnowledgeIngestStatus.DONE
    source = session.get(KnowledgeSource, job.source_id)
    assert source.original_filename is None
    assert session.query(KnowledgeChunk).filter(
        KnowledgeChunk.source_id == source.id).count() == 0
    assert session.query(KnowledgeRelation).filter(
        KnowledgeRelation.source_id == source.id).count() == 0
    with health_schema.health_session(user.id) as hs:
        chunks = hs.scalars(
            select(HealthKnowledgeChunk).where(HealthKnowledgeChunk.source_id == source.id)).all()
        assert [c.text for c in chunks] == ["Диагноз: [[тревожное расстройство]]."]
        relations = hs.scalars(
            select(HealthKnowledgeRelation).where(HealthKnowledgeRelation.source_id == source.id)
        ).all()
        assert [r.to_id for r in relations] == ["тревожное расстройство"]


def test_process_job_health_domain_sanitizes_error_keeps_diagnostic_private(
        session, tmp_path, monkeypatch, health_configured, user):
    job = _make_health_pending_job(session, tmp_path, "Анализ гормонов.pdf",
                                   knowledge_user_id=user.id)

    def _raise(path):
        raise ValueError("страница 2 повреждена: избыток ТТГ")
    monkeypatch.setattr(worker_module, "parse_file", _raise)

    process_job(session, job)
    session.flush()

    assert job.status == KnowledgeIngestStatus.FAILED
    assert job.error == "HEALTH_PARSE_FAILED"
    with health_schema.health_session(user.id) as hs:
        private = hs.get(HealthKnowledgeSourcePrivate, job.source_id)
        assert "ТТГ" in private.parse_error


def test_process_job_health_domain_notifies_owner_with_real_filename(
        session, tmp_path, monkeypatch, health_configured, user):
    job = _make_health_pending_job(session, tmp_path, "Консультация уролога.pdf",
                                   knowledge_user_id=user.id, channel="telegram", recipient="tg:1")
    monkeypatch.setattr(worker_module, "parse_file",
                        lambda path: _FakeParseResult(text="норма", parser="markitdown",
                                                      quality_ok=True))

    process_job(session, job)
    session.flush()

    message = session.scalars(select(OutboxMessage)).one()
    assert "Консультация уролога.pdf" in message.payload_reference["text"]


# ── probe.py — явный health-scope ходит в health-схему ────────────────────

def test_probe_health_domain_finds_chunk_only_in_health_schema(session, health_configured, user):
    ingest_text(session, domain="health", text="Анализ крови показал дефицит железа.",
               knowledge_user_id=user.id)
    session.flush()

    result = probe(session, query="что там с анализом крови", domain="health",
                   knowledge_user_id=user.id)

    assert result.outcome == "LOCAL_ANSWER"


def test_probe_general_query_finds_health_chunk_after_move_to_sidecar(
        session, health_configured, user):
    """Решение владельца 01.09.2026: health отвечает и в общем поиске
    (domain=None), не только через явный domain="health" — даже после
    того, как чанки физически переехали в health-схему, probe() обязан
    заглянуть туда и на обычный вопрос без домена."""
    ingest_text(session, domain="health", text="Анализ крови показал дефицит железа.",
               knowledge_user_id=user.id)
    session.flush()

    result = probe(session, query="что там с анализом крови", knowledge_user_id=user.id)

    assert result.outcome == "LOCAL_ANSWER"


def test_probe_general_query_does_not_answer_health_twice_during_migration(
        session, health_configured, user):
    """Окно миграции R1: чанк уже скопирован в health-схему, но из public
    ещё не удалён (решение владельца — удаляем только после успешного
    бэкапа). Общий вопрос идёт в ОБЕ схемы, и без исключения health из
    public-пути один и тот же текст вернулся бы дважды, съев два слота
    из пяти доступных доказательств."""
    source = ingest_text(session, domain="health",
                         text="Анализ крови показал дефицит железа.",
                         knowledge_user_id=user.id)
    session.flush()
    # То, что физически лежит в public у всех 90 живых health-источников.
    session.add(KnowledgeChunk(
        knowledge_user_id=user.id, source_id=source.id, ordinal=0,
        text="Анализ крови показал дефицит железа.",
        tsv=func.to_tsvector("russian", "Анализ крови показал дефицит железа."),
    ))
    session.flush()

    result = probe(session, query="что там с анализом крови", knowledge_user_id=user.id)

    assert result.outcome == "LOCAL_ANSWER"
    assert len(result.evidence) == 1


def test_probe_general_query_still_excludes_zapiski_client_content(
        session, health_configured, user):
    """Единственное оставшееся исключение из общего поиска — защита
    приватности КЛИЕНТА (simpas/zapiski), не health."""
    ingest_text(session, domain="simpas/zapiski", text="Клиент рассказал про тревогу на работе.",
               knowledge_user_id=user.id)
    session.flush()

    result = probe(session, query="что там про тревогу на работе", knowledge_user_id=user.id)

    assert result.outcome == "NEEDS_REASONING"


# ── §14.16 F15: файлы health вне общего Vault ─────────────────────────────

def test_health_source_path_lands_in_private_tree(session, health_configured, user, tmp_path):
    """§14.16: файловое дерево health обязано быть ВНЕ общего Vault и, в
    частности, не в `<vault>/sources/`. Маршрутизация строки в БД в
    health-схему этого не заменяет."""
    source = ingest_text(session, domain="health", text="Приём эндокринолога.",
                         knowledge_user_id=user.id, vault_root=str(tmp_path))
    session.flush()

    assert not source.source_path.startswith(f"{tmp_path}/")
    assert source.source_path.startswith(f"{tmp_path}-private/health/users/{user.id}/")
    assert not source.raw_path.startswith(f"{tmp_path}/")


def test_non_health_source_path_stays_in_common_vault(session, health_configured, user, tmp_path):
    """Разделение — только для health. Остальные домены остаются там же,
    где были: приватное дерево не должно расползаться на весь Vault."""
    source = ingest_text(session, domain="personal", text="Купил чайник, гарантия два года.",
                         knowledge_user_id=user.id, vault_root=str(tmp_path))
    session.flush()

    assert source.source_path.startswith(f"{tmp_path}/sources/")


def test_ingest_hands_the_atomizer_the_private_root_not_the_common_one(
        session, health_configured, user, tmp_path, monkeypatch):
    """F15, аудит 02.09.2026 — BLOCKER: строка уходила в health-схему, а
    сам .md-файл ложился в общий `<vault>/entities/`.

    Дефект был в ВЫЗЫВАЮЩЕМ коде: `_note_file_path()` получал общий
    корень вместо доменного. Поэтому проверяется именно аргумент, с
    которым `ingest_text()` зовёт атомизатор, а не то, где окажется файл:
    писатель на время rescue заморожен (R2), а контракт вызова — нет, и
    R3 встанет ровно на это место.
    """
    handed = {}
    monkeypatch.setattr(
        ingest_module, "atomize_and_store",
        lambda session, **kw: handed.update(kw) or 0,
    )

    ingest_text(session, domain="health", text="Приём эндокринолога.",
                knowledge_user_id=user.id, vault_root=str(tmp_path))
    session.flush()

    private_root = str(tmp_path.parent / f"{tmp_path.name}-private" / "health" / "users" / str(user.id))
    assert handed["vault_root"] == private_root
    assert not handed["vault_root"].startswith(f"{tmp_path}/")


# ── offboarding.py — окончательное удаление не оставляет health ───────────

def test_delete_user_permanently_removes_health_rows_and_private_tree(
        session, health_configured, user, tmp_path):
    """До R1 health-таблицы были пусты, и пробел был невидим: список
    удаляемых таблиц перечислял только public. После переноса это уже
    медицинский текст, переживающий явное необратимое удаление аккаунта."""
    ingest_text(session, domain="health", text="Приём эндокринолога, повторный.",
                knowledge_user_id=user.id, vault_root=str(tmp_path))
    session.flush()
    private_root = tmp_path.parent / f"{tmp_path.name}-private" / "health" / "users" / str(user.id)
    private_root.mkdir(parents=True, exist_ok=True)
    (private_root / "sources").mkdir(exist_ok=True)
    (private_root / "sources" / "x.md").write_text("приём", encoding="utf-8")

    user.status = KnowledgeUserStatus.SUSPENDED
    session.flush()
    delete_user_permanently(session, knowledge_user_id=user.id, vault_root=str(tmp_path))
    session.flush()

    with health_schema.health_session(user.id) as hs:
        assert hs.scalars(select(HealthKnowledgeChunk)).all() == []
        assert hs.scalars(select(HealthKnowledgeSourcePrivate)).all() == []
    assert not private_root.exists()


# ── documents.py — поиск/скачивание своих health-документов владельцем ───

def test_find_sources_finds_health_document_by_name_via_sidecar(
        session, health_configured, user, tmp_path):
    raw = tmp_path / "raw.bin"
    raw.write_bytes(b"pdf bytes")
    register_file_for_ingest(session, domain="health", raw_path=raw,
                             original_filename="Консультация уролога.pdf",
                             knowledge_user_id=user.id)
    session.flush()

    found = find_sources(session, query="уролога", knowledge_user_id=user.id)

    assert len(found) == 1
    assert found[0].original_filename == "Консультация уролога.pdf"


def test_find_sources_finds_health_document_by_content_via_sidecar(
        session, health_configured, user, tmp_path):
    ingest_text(session, domain="health", text="Дефицит витамина B12 подтверждён анализом.",
               original_filename="Анализ крови.pdf", knowledge_user_id=user.id)
    session.flush()

    found = find_sources(session, query="дефицит витамина", knowledge_user_id=user.id)

    assert [c.original_filename for c in found] == ["Анализ крови.pdf"]


def test_read_original_uses_sidecar_filename_for_health_document(
        session, health_configured, user, tmp_path):
    raw = tmp_path / "raw.bin"
    raw.write_bytes(b"pdf bytes")
    result = register_file_for_ingest(session, domain="health", raw_path=raw,
                                      original_filename="Консультация уролога.pdf",
                                      knowledge_user_id=user.id)
    session.flush()

    original = read_original(session, result.source.id, knowledge_user_id=user.id)

    assert original.filename == "Консультация уролога.pdf"


# ── semantic-v2 в health: изоляция проверяется поведением ─────────────────

def _health_graph(user_id, *, label):
    """Кусок графа v2 целиком внутри health — по штатному пути записи
    (`health_session()`), а не прямым SQL: проверяется то, чем health
    пользуется на самом деле.

    `semantic_run_id` заполняется, но без внешнего ключа: сам прогон
    живёт в public, куда `helm_health` не имеет никаких прав (см.
    докстринг `health_tables.py`).
    """
    run_id = uuid.uuid4()
    source_id = uuid.uuid4()
    with health_schema.health_session(user_id) as hs:
        hs.add(HealthKnowledgeSourcePrivate(
            source_id=source_id, knowledge_user_id=user_id,
            original_filename=f"{label}.pdf", created_at=datetime.now(timezone.utc)))
        hs.flush()
        entity = HealthKnowledgeNode(
            knowledge_user_id=user_id, kind="entity", entity_type="person", subtype="PERSON",
            canonical_label=label, normalized_key=label.lower(), semantic_run_id=run_id)
        event = HealthKnowledgeNode(
            knowledge_user_id=user_id, kind="event", canonical_label=f"визит к {label}",
            statement_text=f"Состоялся визит к {label}.", semantic_run_id=run_id)
        hs.add_all([entity, event])
        hs.flush()
        mention = HealthKnowledgeNodeMention(
            knowledge_user_id=user_id, node_id=entity.id, source_id=source_id,
            evidence_type="extracted", semantic_run_id=run_id)
        hs.add(mention)
        hs.flush()
        hs.add_all([
            HealthKnowledgeEdge(
                knowledge_user_id=user_id, from_node_id=event.id, to_node_id=entity.id,
                relation_type="involves", role="doctor", source_id=source_id,
                mention_id=mention.id, evidence_type="extracted", semantic_run_id=run_id),
            HealthKnowledgeEntityAlias(
                knowledge_user_id=user_id, entity_node_id=entity.id,
                alias=label, normalized_alias=label.lower(), source_id=source_id),
        ])


HEALTH_SEMANTIC_MODELS = (
    HealthKnowledgeNode, HealthKnowledgeNodeMention,
    HealthKnowledgeEdge, HealthKnowledgeEntityAlias,
)


def test_health_semantic_v2_is_isolated_between_tenants(session, health_configured, user):
    """То же поведенческое требование, что и в public, но по health-пути.

    Проверяется не «политика включена», а «сосед не виден»: политика с
    неверным предикатом тоже показывает `t`. Здесь два владельца, строки
    во всех четырёх health-таблицах semantic-v2 у каждого, и три
    счётчика — A→B, B→A и «тенант не выставлен».
    """
    second = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(second)
    session.flush()
    first_id, second_id = user.id, second.id
    session.commit()

    labels = {first_id: "Безручко Дарья Юрьевна", second_id: "Бокова Мария Николаевна"}
    for user_id, label in labels.items():
        _health_graph(user_id, label=label)

    for mine, theirs in ((first_id, second_id), (second_id, first_id)):
        with health_schema.health_session(mine) as hs:
            for model in HEALTH_SEMANTIC_MODELS:
                rows = hs.scalars(select(model)).all()
                assert rows, f"{model.__name__}: свои строки не видны, тест ничего не проверяет"
                assert not [r for r in rows if r.knowledge_user_id != mine], (
                    f"{model.__name__}: виден граф владельца {theirs}")
            seen = {n.canonical_label for n in hs.scalars(select(HealthKnowledgeNode)).all()}
            assert labels[theirs] not in " ".join(seen)

    with health_schema.health_session(first_id) as hs:
        hs.execute(text("SET LOCAL app.current_knowledge_user_id = ''"))
        for model in HEALTH_SEMANTIC_MODELS:
            assert hs.scalars(select(model)).all() == [], model.__name__


def test_health_writer_preserves_entity_type_and_statement_text(session, health_configured, user):
    """R3.1 round-trip через ЗДОРОВЬЕ-путь публикации, не только public.

    Владелец потребовал минимум один такой тест: health пишет через
    отдельную роль по отдельному соединению (`health_session()`), и «в
    public проверено» этого пути не касается — тот же класс дефекта мог
    остаться именно здесь, если бы фикс тронул только public-ветку кода
    (а `_write_extraction()` — общая для обеих схем функция, так что
    падение здесь означало бы, что общий код и общая модель разошлись).
    """
    source = ingest_text(session, domain="health", text="Приём эндокринолога.",
                         knowledge_user_id=user.id)
    session.flush()

    extraction = WindowExtraction(
        entities=[ExtractedEntity(local_id="e1", entity_type="PERSON", subtype="doctor",
                                  label="Бокова Мария Николаевна")],
        atoms=[ExtractedAtom(local_id="a1", kind="fact", title="Диагноз",
                             text="Выявлен дефицит железа, назначена терапия.")],
    )
    result = publish_semantic_run(
        session, source=source, text="Приём эндокринолога.",
        extract=lambda *a, **kw: extraction)
    session.commit()
    assert result.status == SemanticRunStatus.READY

    with health_schema.health_session(user.id) as hs:
        nodes = {n.kind: n for n in hs.scalars(
            select(HealthKnowledgeNode).where(
                HealthKnowledgeNode.semantic_run_id == result.run_id)).all()}

    assert nodes["entity"].entity_type == "person"
    assert nodes["entity"].subtype == "doctor"
    assert nodes["entity"].statement_text is None
    assert nodes["fact"].statement_text == "Выявлен дефицит железа, назначена терапия."
    assert nodes["fact"].canonical_label == "Диагноз"
    assert nodes["fact"].entity_type is None


def test_health_semantic_v2_registry_is_closed(session, health_configured, user):
    """Реестр §14.9 закрыт и в health-схеме тоже. Отдельная роль, отдельное
    соединение — «в public проверим» её не касается."""
    source_id = uuid.uuid4()
    with health_schema.health_session(user.id) as hs:
        hs.add(HealthKnowledgeSourcePrivate(
            source_id=source_id, knowledge_user_id=user.id,
            original_filename="приём.pdf", created_at=datetime.now(timezone.utc)))
        hs.flush()

    with pytest.raises(sqlalchemy.exc.IntegrityError) as err:
        with health_schema.health_session(user.id) as hs:
            hs.add(HealthKnowledgeNode(
                knowledge_user_id=user.id, kind="нет", canonical_label="x",
                semantic_run_id=uuid.uuid4()))
            hs.flush()
    assert "ck_knowledge_nodes_kind" in str(err.value)
