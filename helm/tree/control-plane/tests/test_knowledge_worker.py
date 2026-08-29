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
from helm_core.knowledge.worker import claim_next_job, process_job
from helm_core.models import KnowledgeChunk, KnowledgeIngestJob, KnowledgeIngestStatus, KnowledgeSource, KnowledgeStatus


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

def _make_pending_job(session, tmp_path, name: str) -> KnowledgeIngestJob:
    raw = tmp_path / name
    raw.write_text(f"содержимое {name}", encoding="utf-8")
    # vault_root=tmp_path: process_job() пишет L1 SOURCE .md на диск —
    # /opt/helm-knowledge не должен трогаться при прогоне тестов.
    result = register_file_for_ingest(session, domain="engineering", raw_path=raw,
                                      original_filename=name, vault_root=str(tmp_path))
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
    assert Path(source.source_path).read_text(encoding="utf-8") == "Решение: используем Postgres."
    chunks = session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id)).all()
    assert [c.text for c in chunks] == ["Решение: используем Postgres."]


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
