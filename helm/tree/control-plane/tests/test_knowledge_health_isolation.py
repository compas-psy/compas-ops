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

import pytest
from sqlalchemy import func, select, text

from helm_core.config import get_settings
from helm_core.knowledge import atomizer
from helm_core.knowledge import health_schema
from helm_core.knowledge import worker as worker_module
from helm_core.knowledge.atomizer import AtomizedAtom
from helm_core.knowledge.documents import find_sources, read_original
from helm_core.knowledge.ingest import ingest_text, register_file_for_ingest
from helm_core.knowledge.probe import probe
from helm_core.knowledge.worker import process_job
from helm_core.models import (
    HealthKnowledgeChunk, HealthKnowledgeNote, HealthKnowledgeRelation, HealthKnowledgeSourcePrivate,
    KnowledgeChunk, KnowledgeIngestStatus, KnowledgeNote, KnowledgeRelation, KnowledgeSource,
    KnowledgeUser, KnowledgeUserRole, OutboxMessage,
)
from helm_core.models.health_tables import HealthBase
from conftest import DB_URL

HEALTH_TABLES = ("knowledge_relations", "knowledge_chunks", "knowledge_source_private", "knowledge_notes")


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
        conn.execute(text(
            "TRUNCATE health.knowledge_relations, health.knowledge_chunks, "
            "health.knowledge_notes, health.knowledge_source_private RESTART IDENTITY CASCADE"
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

def test_ingest_text_health_domain_routes_notes_to_sidecar(
        session, tmp_path, monkeypatch, health_configured, user):
    """Атомизатор не содержит ветки "если health" — маршрутизация приходит
    из is_health_domain()/health_schema_configured(), как у chunks/
    relations выше. Здесь проверяется РЕЗУЛЬТАТ этой же маршрутизации для
    knowledge_notes, не новая логика."""
    monkeypatch.setattr(
        atomizer, "atomize_or_empty",
        lambda text, *, domain: [AtomizedAtom(slug="Иванов, уролог", type="PERSON",
                                              text="Приём у уролога Иванова.", links=())],
    )

    source = ingest_text(session, domain="health", text="Консультация уролога Иванова.",
                         knowledge_user_id=user.id, vault_root=str(tmp_path))
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


def test_health_semantic_note_file_is_not_written_into_common_vault(
        session, health_configured, user, tmp_path, monkeypatch):
    """F15, аудит 02.09.2026 — BLOCKER: `_note_file_path()` собирал путь
    из типа и slug без домена, и health-заметка ложилась в общий
    `<vault>/entities/`, хотя строка в БД уходила в health-схему."""
    monkeypatch.setattr(
        atomizer, "atomize_or_empty",
        lambda text, *, domain: [AtomizedAtom(slug="Бокова Мария Николаевна", type="PERSON",
                                              text="Врач эндокринолог.", links=())],
    )

    ingest_text(session, domain="health", text="Приём эндокринолога.",
                knowledge_user_id=user.id, vault_root=str(tmp_path))
    session.flush()

    assert not (tmp_path / "entities" / "Бокова Мария Николаевна.md").exists()
    private = (tmp_path.parent / f"{tmp_path.name}-private" / "health" / "users" / str(user.id)
               / "entities" / "Бокова Мария Николаевна.md")
    assert private.is_file()
    assert "Врач эндокринолог." in private.read_text(encoding="utf-8")


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
