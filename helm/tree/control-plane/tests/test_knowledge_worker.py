"""P8.5.2 — register_file_for_ingest() + async worker (§14.5.1, §14.6).

Оркестрация (claim/process, переходы статусов) тестируется без реальных
парсеров — `parse_file()` подменяется (monkeypatch), потому что именно
эта логика — состояния job/source, что происходит при провале quality
gate или исключении — самая подверженная ошибкам часть, и её можно
проверить полностью без markitdown/docling. Сам parse_file() на реальных
файлах — tests/test_knowledge_parsers.py.
"""

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from helm_core.knowledge.ingest import register_file_for_ingest
from helm_core.knowledge import worker as worker_module
from helm_core.knowledge.worker import _frontmatter, claim_next_job, process_job
from helm_core.models import (
    KnowledgeChunk, KnowledgeIngestJob, KnowledgeIngestStatus, KnowledgeSource, KnowledgeStatus,
    OutboxMessage,
)


# ── register_file_for_ingest() ────────────────────────────────────────────

def test_register_file_for_ingest_creates_source_and_pending_job(session, tmp_path):
    raw = tmp_path / "sample.txt"
    raw.write_text("Решение: используем Postgres.", encoding="utf-8")

    result = register_file_for_ingest(session, domain="engineering", raw_path=raw,
                                      original_filename="sample.txt")
    session.flush()

    assert result.created is True
    assert result.source.status == KnowledgeStatus.ACTIVE
    assert result.source.parser is None  # ещё не распарсен
    assert result.job is not None
    assert result.job.status == KnowledgeIngestStatus.PENDING
    assert result.job.source_id == result.source.id


def test_register_file_for_ingest_dedups_by_sha256(session, tmp_path):
    raw = tmp_path / "sample.txt"
    raw.write_text("Тот же самый текст.", encoding="utf-8")

    first = register_file_for_ingest(session, domain="engineering", raw_path=raw)
    session.flush()
    second = register_file_for_ingest(session, domain="engineering", raw_path=raw)
    session.flush()

    assert second.created is False
    assert second.job is None
    assert second.source.id == first.source.id
    assert len(session.scalars(select(KnowledgeIngestJob)).all()) == 1


# ── claim_next_job() ────────────────────────────────────────────────────

def _make_pending_job(session, tmp_path, name: str, *, channel: str | None = None,
                      recipient: str | None = None,
                      knowledge_user_id=None) -> KnowledgeIngestJob:
    raw = tmp_path / name
    raw.write_text(f"содержимое {name}", encoding="utf-8")
    # vault_root=tmp_path: process_job() пишет L1 SOURCE .md на диск —
    # /opt/helm-knowledge не должен трогаться при прогоне тестов.
    result = register_file_for_ingest(session, domain="engineering", raw_path=raw,
                                      original_filename=name, vault_root=str(tmp_path),
                                      channel=channel, recipient=recipient,
                                      knowledge_user_id=knowledge_user_id)
    session.flush()
    return result.job


def test_claim_next_job_returns_none_when_empty(session):
    assert claim_next_job(session) is None


def test_claim_next_job_returns_oldest_pending_and_marks_running(session, tmp_path):
    first = _make_pending_job(session, tmp_path, "a.txt")
    _make_pending_job(session, tmp_path, "b.txt")

    claimed = claim_next_job(session)
    session.flush()

    assert claimed.id == first.id
    assert claimed.status == KnowledgeIngestStatus.RUNNING


def test_claim_next_job_is_fair_round_robin_across_tenants(session, tmp_path):
    """v3.8 §14.4 fair queue: "one user's large ZIP does not starve
    another user's short upload" — большой бэклог одного тенанта не
    должен откладывать единственный job другого до полного исчерпания
    первого."""
    from helm_core.models import KnowledgeUser, KnowledgeUserRole

    other = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(other)
    session.flush()

    owner_j1 = _make_pending_job(session, tmp_path, "owner-1.txt")
    owner_j2 = _make_pending_job(session, tmp_path, "owner-2.txt")
    other_j1 = _make_pending_job(session, tmp_path, "other-1.txt", knowledge_user_id=other.id)

    worker_module._last_served_tenant_index = -1  # детерминизм: не зависеть от порядка других тестов

    first = claim_next_job(session)
    second = claim_next_job(session)
    third = claim_next_job(session)
    fourth = claim_next_job(session)

    # SYSTEM_OWNER создан раньше other (seed-фикстура), поэтому первый в
    # ротации — но other's единственный job обслуживается ВТОРЫМ, не
    # после того, как у owner закончится вся его очередь.
    assert first.id == owner_j1.id
    assert second.id == other_j1.id
    assert third.id == owner_j2.id
    assert fourth is None


# ── §14.3 markdown contract: YAML frontmatter ─────────────────────────────

def test_frontmatter_contains_required_fields_self_referencing(session, tmp_path):
    raw = tmp_path / "note.txt"
    raw.write_text("x", encoding="utf-8")
    result = register_file_for_ingest(session, domain="health", raw_path=raw)
    session.flush()
    source = result.source

    fm = _frontmatter(source)

    assert fm.startswith("---\n")
    assert fm.count("---") == 2, "ровно один блок frontmatter, не больше"
    assert f"id: {source.id}" in fm
    assert "type: source" in fm
    assert "domain: health" in fm
    assert f'source_ids: ["{source.id}"]' in fm, "L1 SOURCE ссылается сама на себя"
    assert f'source_sha256: ["{source.sha256}"]' in fm
    assert "sensitivity: internal" in fm
    assert "trust: extracted" in fm
    assert "status: active" in fm
    # §14.3: confidence/supersedes/contradicts — только для derived/L2 note.
    assert "confidence" not in fm
    assert "supersedes" not in fm
    assert "contradicts" not in fm


# ── process_job() — оркестрация, parse_file() подменён ────────────────────

@dataclass
class _FakeParseResult:
    text: str
    parser: str
    quality_ok: bool


def test_process_job_success_creates_chunks_and_marks_done(session, tmp_path, monkeypatch):
    job = _make_pending_job(session, tmp_path, "doc.txt")
    monkeypatch.setattr(worker_module, "parse_file",
                        lambda path: _FakeParseResult(text="Решение: используем Postgres.",
                                                      parser="markitdown", quality_ok=True))

    process_job(session, job)
    session.flush()

    assert job.status == KnowledgeIngestStatus.DONE
    source = session.get(KnowledgeSource, job.source_id)
    assert source.parser == "markitdown"
    assert source.status == KnowledgeStatus.ACTIVE
    # L1 SOURCE (§14.1/§14.2) — реальный .md-файл, не только строки в БД.
    written = Path(source.source_path).read_text(encoding="utf-8")
    # §14.3 markdown contract — YAML frontmatter обязателен для каждой
    # normalized note (id/type/domain/sensitivity/trust/status/...), не
    # только сам извлечённый текст — иначе Vault, открытый напрямую
    # (Obsidian/SFTP), не отличает health/client_restricted файлы от прочих.
    assert written.startswith("---\n")
    assert f"id: {source.id}" in written
    assert "type: source" in written
    assert "domain: engineering" in written
    assert "sensitivity: internal" in written
    assert "trust: extracted" in written
    assert "status: active" in written
    assert written.endswith("Решение: используем Postgres.")
    chunks = session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id)).all()
    assert [c.text for c in chunks] == ["Решение: используем Postgres."]


def test_process_job_binds_rls_from_the_jobs_own_tenant_not_another_users(session, tmp_path,
                                                                          monkeypatch):
    """v3.8 §14.4: "worker tests must prove it never processes a source for
    another user" — job.knowledge_user_id (immutable) — единственный
    источник тенанта для process_job(), не какой-то текущий GUC сессии.
    Второй пользователь заведён и его GUC выставлен ДО claim/process,
    чтобы явно проверить, что process_job() сам переключает привязку на
    тенанта job'а, а не наследует чужую от вызывающего кода."""
    from helm_core.knowledge.tenancy import bind_knowledge_user
    from helm_core.models import KnowledgeUser, KnowledgeUserRole

    other_user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(other_user)
    session.flush()

    job = _make_pending_job(session, tmp_path, "owner-doc.txt")
    assert job.knowledge_user_id != other_user.id  # owner-задача, не other_user

    bind_knowledge_user(session, other_user.id)  # вызывающий код смотрит НЕ на owner
    monkeypatch.setattr(worker_module, "parse_file",
                        lambda path: _FakeParseResult(text="Решение: используем Postgres.",
                                                      parser="markitdown", quality_ok=True))

    process_job(session, job)
    session.flush()

    assert job.status == KnowledgeIngestStatus.DONE
    source = session.get(KnowledgeSource, job.source_id)
    assert source.knowledge_user_id == job.knowledge_user_id
    chunks = session.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id)
    ).all()
    assert chunks and all(c.knowledge_user_id == job.knowledge_user_id for c in chunks)

    # RLS: с GUC other_user'а созданный source/chunk невидимы вообще.
    # expunge_all() обязателен: иначе session.get() либо вернул бы объект
    # из identity map в памяти, не сходив в БД (RLS не проверился бы), либо
    # (после expire_all()) ORM истолковал бы "RLS скрыла строку" как
    # ObjectDeletedError — с точки зрения identity map это неотличимо от
    # реального удаления. expunge_all() заставляет трактовать PK как
    # "не видели вовсе", и тогда пустой результат — просто None.
    bind_knowledge_user(session, other_user.id)
    session.expunge_all()
    assert session.get(KnowledgeSource, source.id) is None
    assert session.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id)
    ).all() == []


def test_process_job_failure_after_successful_parse_marks_failed_not_crash(session, tmp_path, monkeypatch):
    """НАЙДЕНО на живом смоук-тесте 29.08.2026: раньше try/except в
    process_job() оборачивал только parse_file() — исключение на ЛЮБОМ
    шаге после (запись L1 SOURCE на диск упала PermissionError на
    реальном сервере) улетало необработанным в run_forever(), валило
    процесс, транзакция откатывалась (job снова PENDING), Docker
    поднимал контейнер заново — и тот падал на ТОЙ ЖЕ задаче: вечный
    краш-луп вместо FAILED на одной плохой задаче."""
    job = _make_pending_job(session, tmp_path, "doc.txt")
    monkeypatch.setattr(worker_module, "parse_file",
                        lambda path: _FakeParseResult(text="Решение: используем Postgres.",
                                                      parser="markitdown", quality_ok=True))

    def _raise_write(self, *a, **kw):
        raise PermissionError("permission denied (тест)")

    monkeypatch.setattr(Path, "write_text", _raise_write)

    process_job(session, job)  # не должно поднять исключение наружу
    session.flush()

    assert job.status == KnowledgeIngestStatus.FAILED
    assert "permission denied" in job.error


def test_process_job_quality_fail_marks_needs_review_without_chunks(session, tmp_path, monkeypatch):
    job = _make_pending_job(session, tmp_path, "bad.pdf")
    monkeypatch.setattr(worker_module, "parse_file",
                        lambda path: _FakeParseResult(text="nnnnnnn nnnnnnnnnn",
                                                      parser="docling", quality_ok=False))

    process_job(session, job)
    session.flush()

    assert job.status == KnowledgeIngestStatus.NEEDS_REVIEW
    source = session.get(KnowledgeSource, job.source_id)
    assert source.status == KnowledgeStatus.NEEDS_REVIEW
    assert session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id)).all() == []


def test_process_job_parse_exception_marks_failed_with_error(session, tmp_path, monkeypatch):
    job = _make_pending_job(session, tmp_path, "corrupt.docx")

    def _raise(path):
        raise ValueError("повреждённый файл")

    monkeypatch.setattr(worker_module, "parse_file", _raise)

    process_job(session, job)
    session.flush()

    assert job.status == KnowledgeIngestStatus.FAILED
    assert "повреждённый файл" in job.error


def test_process_job_missing_source_marks_failed(session):
    job = KnowledgeIngestJob(source_id=uuid.uuid4(), status=KnowledgeIngestStatus.RUNNING)
    # source_id — внешний ключ; вставлять некуда не пытаемся, process_job
    # обязан сам проверить существование через session.get() до записи.
    process_job(session, job)

    assert job.status == KnowledgeIngestStatus.FAILED
    assert "не найден" in job.error


# ── P8.5.7, "3 шага": уведомление владельца по завершении разбора ─────────

def test_process_job_done_with_recipient_enqueues_completion_notice(session, tmp_path, monkeypatch):
    job = _make_pending_job(session, tmp_path, "doc.txt", channel="max", recipient="777")
    monkeypatch.setattr(worker_module, "parse_file",
                        lambda path: _FakeParseResult(text="Решение: используем Postgres.",
                                                      parser="markitdown", quality_ok=True))

    process_job(session, job)
    session.flush()

    message = session.scalars(select(OutboxMessage)).one()
    assert message.channel == "max"
    assert message.recipient == "777"
    assert "doc.txt" in message.payload_reference["text"]
    assert "завершён" in message.payload_reference["text"]


def test_process_job_needs_review_with_recipient_enqueues_notice(session, tmp_path, monkeypatch):
    job = _make_pending_job(session, tmp_path, "bad.pdf", channel="max", recipient="777")
    monkeypatch.setattr(worker_module, "parse_file",
                        lambda path: _FakeParseResult(text="nnnnnnn", parser="docling",
                                                      quality_ok=False))

    process_job(session, job)
    session.flush()

    message = session.scalars(select(OutboxMessage)).one()
    assert "не удался" in message.payload_reference["text"]


def test_process_job_failed_with_recipient_enqueues_notice(session, tmp_path, monkeypatch):
    job = _make_pending_job(session, tmp_path, "corrupt.docx", channel="max", recipient="777")
    monkeypatch.setattr(worker_module, "parse_file",
                        lambda path: (_ for _ in ()).throw(ValueError("повреждённый файл")))

    process_job(session, job)
    session.flush()

    message = session.scalars(select(OutboxMessage)).one()
    assert "ошибкой" in message.payload_reference["text"]


def test_process_job_without_recipient_does_not_notify(session, tmp_path, monkeypatch):
    """ingest_text()/тестовые пути не задают channel/recipient — уведомлять
    некого, тихий no-op, а не KeyError/попытка отправить в никуда."""
    job = _make_pending_job(session, tmp_path, "doc.txt")  # channel=None, recipient=None
    monkeypatch.setattr(worker_module, "parse_file",
                        lambda path: _FakeParseResult(text="текст", parser="markitdown",
                                                      quality_ok=True))

    process_job(session, job)
    session.flush()

    assert session.scalars(select(OutboxMessage)).all() == []
