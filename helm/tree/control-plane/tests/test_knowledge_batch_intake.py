"""ZIP batch ingest (v3.7 §14.4.0/14.5.1-2, P8.5.2.1) — acceptance tests
из CONTINUE_HELM_TO_v3.7_ZIP_BATCH_INGEST.md §10 + "Final critical
clarifications". `parse_file()` подменяется тем же паттерном, что уже
есть в `test_knowledge_worker.py` — оркестрация тестируется без реальных
markitdown/docling."""

import zipfile
from dataclasses import dataclass

import pytest
from sqlalchemy import select

from helm_core.knowledge import worker as worker_module
from helm_core.knowledge.batch_intake import (
    cancel_remaining, disable_created_sources, finalize_batch_if_terminal,
    resolve_batch_domain, retry_failed, stage_batch,
)
from helm_core.knowledge.worker import claim_next_job, process_job
from helm_core.models import (
    KnowledgeBatchItem, KnowledgeBatchItemStatus, KnowledgeBatchStatus,
    KnowledgeIngestBatch, KnowledgeIngestJob, KnowledgeSource, KnowledgeStatus, OutboxMessage,
)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


@dataclass
class _FakeParseResult:
    text: str
    parser: str = "markitdown"
    quality_ok: bool = True


def _run_worker_to_completion(session, monkeypatch, tmp_path, *, max_iterations=20):
    """Гоняет claim_next_job/process_job до опустошения очереди — та же
    петля, что worker.run_forever(), без реального sleep/процесса."""
    monkeypatch.setattr(worker_module, "parse_file",
                        lambda path: _FakeParseResult(text=f"разобрано: {path.name}"))
    for _ in range(max_iterations):
        job = claim_next_job(session)
        if job is None:
            break
        process_job(session, job)
        session.flush()


def _stage_and_resolve(session, tmp_path, monkeypatch, entries, *, domain="engineering",
                       channel="telegram", recipient="12345"):
    vault_root = str(tmp_path / "vault")
    raw_batches_root = str(tmp_path / "raw-batches")
    data = _zip_bytes(entries)
    staged = stage_batch(session, channel=channel, data=data, original_filename="test.zip",
                         mime_type="application/zip", recipient=recipient,
                         raw_batches_root=raw_batches_root)
    session.flush()
    assert staged.waiting_for_domain is True
    outcome = resolve_batch_domain(session, channel=channel, reply_text=domain,
                                   vault_root=vault_root)
    session.flush()
    return staged, outcome, vault_root


# ── 1. normal ZIP -> N child jobs -> existing pipeline ─────────────────────

def test_normal_zip_creates_child_job_per_eligible_member(session, tmp_path, monkeypatch):
    staged, outcome, vault_root = _stage_and_resolve(
        session, tmp_path, monkeypatch, {"one.txt": b"first", "two.txt": b"second"})

    assert outcome.status == "queued"
    items = session.scalars(select(KnowledgeBatchItem)
                            .where(KnowledgeBatchItem.batch_id == outcome.batch.id)).all()
    assert len(items) == 2
    assert all(i.status == KnowledgeBatchItemStatus.QUEUED for i in items)
    jobs = session.scalars(select(KnowledgeIngestJob)).all()
    assert len(jobs) == 2
    assert all(j.batch_item_id is not None for j in jobs)


def test_full_pipeline_end_to_end_marks_batch_completed(session, tmp_path, monkeypatch):
    _, outcome, vault_root = _stage_and_resolve(
        session, tmp_path, monkeypatch, {"one.txt": b"first", "two.txt": b"second"})

    _run_worker_to_completion(session, monkeypatch, tmp_path)
    batch = finalize_batch_if_terminal(session, outcome.batch.id)

    assert batch.status == KnowledgeBatchStatus.COMPLETED
    assert batch.ready_count == 2
    assert batch.chunk_count_total > 0


# ── 2. same domain chosen only once ────────────────────────────────────────

def test_domain_asked_once_all_members_inherit_it(session, tmp_path, monkeypatch):
    _, outcome, vault_root = _stage_and_resolve(
        session, tmp_path, monkeypatch,
        {"one.txt": b"first", "two.txt": b"second", "three.txt": b"third"},
        domain="health")

    sources = [session.get(KnowledgeSource, i.source_id)
              for i in session.scalars(select(KnowledgeBatchItem)
                                       .where(KnowledgeBatchItem.batch_id == outcome.batch.id)).all()]
    assert all(s.domain == "health" for s in sources)
    assert outcome.batch.domain == "health"


# ── 3. zip-slip/symlink/bomb/encrypted blocked safely (integration level;
#      unit coverage in test_knowledge_zip_safety.py) ─────────────────────

def test_encrypted_archive_blocks_whole_batch_at_stage(session, tmp_path):
    data = bytearray(_zip_bytes({"secret.txt": b"x"}))
    data[data.index(b"PK\x03\x04") + 6] |= 0x1
    data[data.index(b"PK\x01\x02") + 8] |= 0x1

    staged = stage_batch(session, channel="telegram", data=bytes(data),
                         original_filename="enc.zip", mime_type="application/zip",
                         raw_batches_root=str(tmp_path / "raw-batches"))
    session.flush()

    assert staged.waiting_for_domain is False
    assert staged.batch.status == KnowledgeBatchStatus.BLOCKED
    assert staged.batch.error_code == "BLOCKED_ENCRYPTED"


def test_traversal_member_quarantined_siblings_still_processed(session, tmp_path):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("good.txt", b"fine content")
        zf.writestr("../../etc/passwd", b"pwned")
    data = buf.getvalue()

    staged = stage_batch(session, channel="max", data=data, original_filename="mixed.zip",
                         mime_type="application/zip", raw_batches_root=str(tmp_path / "raw-batches"))
    session.flush()
    outcome = resolve_batch_domain(session, channel="max", reply_text="engineering",
                                   vault_root=str(tmp_path / "vault"))
    session.flush()

    items = session.scalars(select(KnowledgeBatchItem)
                            .where(KnowledgeBatchItem.batch_id == outcome.batch.id)).all()
    statuses = {i.archive_member_path_original: i.status for i in items}
    assert statuses["good.txt"] == KnowledgeBatchItemStatus.QUEUED
    assert statuses["../../etc/passwd"] == KnowledgeBatchItemStatus.QUARANTINE


# ── 4. nested ZIP skipped, not recursively expanded ────────────────────────

def test_nested_zip_member_is_skipped_not_expanded(session, tmp_path, monkeypatch):
    inner = _zip_bytes({"inner.txt": b"inner content"})
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("outer.txt", b"outer content")
        zf.writestr("nested.zip", inner)
    data = buf.getvalue()

    staged = stage_batch(session, channel="telegram", data=data, original_filename="a.zip",
                         mime_type="application/zip", raw_batches_root=str(tmp_path / "rb"))
    session.flush()
    outcome = resolve_batch_domain(session, channel="telegram", reply_text="engineering",
                                   vault_root=str(tmp_path / "vault"))
    session.flush()

    items = {i.archive_member_path_original: i.status
            for i in session.scalars(select(KnowledgeBatchItem)
                                     .where(KnowledgeBatchItem.batch_id == outcome.batch.id)).all()}
    assert items["nested.zip"] == KnowledgeBatchItemStatus.SKIPPED_NESTED_ARCHIVE
    assert items["outer.txt"] == KnowledgeBatchItemStatus.QUEUED
    # "inner.txt" никогда не появляется как отдельный item — не распаковано рекурсивно.
    assert "inner.txt" not in items


# ── 5. exact duplicate child is not reparsed ───────────────────────────────

def test_member_matching_existing_source_is_exact_duplicate_no_second_job(session, tmp_path, monkeypatch):
    from helm_core.knowledge.ingest import register_file_for_ingest
    raw = tmp_path / "pre-existing.txt"
    raw.write_text("уже есть в базе", encoding="utf-8")
    vault_root = str(tmp_path / "vault")
    register_file_for_ingest(session, domain="engineering", raw_path=raw,
                             original_filename="pre-existing.txt", vault_root=vault_root)
    session.flush()
    assert len(session.scalars(select(KnowledgeIngestJob)).all()) == 1

    staged = stage_batch(session, channel="telegram",
                         data=_zip_bytes({"same.txt": "уже есть в базе".encode("utf-8")}),
                         original_filename="a.zip", mime_type="application/zip",
                         raw_batches_root=str(tmp_path / "rb"))
    session.flush()
    outcome = resolve_batch_domain(session, channel="telegram", reply_text="engineering",
                                   vault_root=vault_root)
    session.flush()

    item = session.scalars(select(KnowledgeBatchItem)
                           .where(KnowledgeBatchItem.batch_id == outcome.batch.id)).one()
    assert item.status == KnowledgeBatchItemStatus.EXACT_DUPLICATE
    assert item.source_created_by_batch is False
    # Дедуп по sha256 глобальный — не создалась вторая ingest job.
    assert len(session.scalars(select(KnowledgeIngestJob)).all()) == 1


# ── 6. same filename/different bytes IS parsed ─────────────────────────────

def test_same_filename_different_bytes_across_batches_both_parsed(session, tmp_path, monkeypatch):
    vault_root = str(tmp_path / "vault")
    _, outcome1, _ = _stage_and_resolve(session, tmp_path, monkeypatch,
                                        {"report.txt": b"version one"})
    staged2 = stage_batch(session, channel="telegram", data=_zip_bytes({"report.txt": b"version two, totally different"}),
                          original_filename="b.zip", mime_type="application/zip",
                          raw_batches_root=str(tmp_path / "rb2"))
    session.flush()
    outcome2 = resolve_batch_domain(session, channel="telegram", reply_text="engineering",
                                    vault_root=vault_root)
    session.flush()

    item2 = session.scalars(select(KnowledgeBatchItem)
                            .where(KnowledgeBatchItem.batch_id == outcome2.batch.id)).one()
    assert item2.status == KnowledgeBatchItemStatus.QUEUED
    assert item2.source_created_by_batch is True


# ── 7. one broken member does not roll back siblings ───────────────────────

def test_one_member_parse_failure_does_not_block_siblings(session, tmp_path, monkeypatch):
    _, outcome, vault_root = _stage_and_resolve(
        session, tmp_path, monkeypatch, {"good.txt": b"fine", "bad.txt": b"MARKER_SHOULD_FAIL"})

    def flaky_parse(path):
        # Файлы на диске адресуются по sha256, не по исходному имени в
        # архиве (anti zip-slip) — различаем по содержимому, не по имени.
        if b"MARKER_SHOULD_FAIL" in path.read_bytes():
            raise RuntimeError("парсер упал на этом файле")
        return _FakeParseResult(text="ok")

    monkeypatch.setattr(worker_module, "parse_file", flaky_parse)
    for _ in range(10):
        job = claim_next_job(session)
        if job is None:
            break
        process_job(session, job)
        session.flush()

    batch = finalize_batch_if_terminal(session, outcome.batch.id)
    assert batch.status == KnowledgeBatchStatus.COMPLETED_WITH_ERRORS
    assert batch.ready_count == 1
    assert batch.failed_count == 1


# ── 8. reboot halfway resumes without duplicate work ───────────────────────

def test_finalize_is_idempotent_across_repeated_calls(session, tmp_path, monkeypatch):
    _, outcome, vault_root = _stage_and_resolve(
        session, tmp_path, monkeypatch, {"one.txt": b"a"})

    _run_worker_to_completion(session, monkeypatch, tmp_path)
    first = finalize_batch_if_terminal(session, outcome.batch.id)
    first_sent_at = first.final_notification_sent_at
    assert first_sent_at is not None

    # "reboot" — вызываем повторно, как это сделал бы новый процесс воркера.
    second = finalize_batch_if_terminal(session, outcome.batch.id)
    assert second.final_notification_sent_at == first_sent_at

    # dedup_key в БД — sha256-хэш материала (outbox.dedup_key()), не сам
    # batch_id текстом — фильтруем по channel/recipient, тест сам создал
    # ровно один batch на этот канал/адресата.
    outbox_rows = session.scalars(
        select(OutboxMessage).where(OutboxMessage.channel == "telegram",
                                    OutboxMessage.recipient == "12345")
    ).all()
    assert len(outbox_rows) == 1


# ── 9. exactly one final notification is delivered ─────────────────────────

def test_exactly_one_final_outbox_message_with_expected_dedup_key(session, tmp_path, monkeypatch):
    _, outcome, vault_root = _stage_and_resolve(
        session, tmp_path, monkeypatch, {"one.txt": b"a", "two.txt": b"b"},
        recipient="999")

    _run_worker_to_completion(session, monkeypatch, tmp_path)
    batch = finalize_batch_if_terminal(session, outcome.batch.id)

    row = session.scalar(select(OutboxMessage).where(OutboxMessage.channel == "telegram",
                                                      OutboxMessage.recipient == "999"))
    assert row is not None
    assert row.dedup_key == __import__("hashlib").sha256(
        f"telegram\x00999\x00knowledge_batch_final:{batch.id}:0".encode()
    ).hexdigest()


# ── 10. retry_failed does not touch READY/DUPLICATE children ──────────────

def test_retry_failed_only_touches_failed_retryable_not_ready_or_duplicate(session, tmp_path, monkeypatch):
    from helm_core.knowledge.ingest import register_file_for_ingest
    vault_root = str(tmp_path / "vault")
    pre = tmp_path / "dup-source.txt"
    pre.write_text("контент дубля", encoding="utf-8")
    register_file_for_ingest(session, domain="engineering", raw_path=pre,
                             original_filename="dup-source.txt", vault_root=vault_root)
    session.flush()

    staged, outcome, _ = _stage_and_resolve(
        session, tmp_path, monkeypatch,
        {"ok.txt": b"fine", "will-fail.txt": b"MARKER_BOOM", "dup.txt": "контент дубля".encode("utf-8")})

    def flaky_parse(path):
        if b"MARKER_BOOM" in path.read_bytes():
            raise RuntimeError("boom")
        return _FakeParseResult(text="ok")

    monkeypatch.setattr(worker_module, "parse_file", flaky_parse)
    for _ in range(10):
        job = claim_next_job(session)
        if job is None:
            break
        process_job(session, job)
        session.flush()

    items_before = {i.archive_member_path_original: (i.status, i.updated_at)
                   for i in session.scalars(select(KnowledgeBatchItem)
                                            .where(KnowledgeBatchItem.batch_id == outcome.batch.id)).all()}
    assert items_before["ok.txt"][0] == KnowledgeBatchItemStatus.READY
    assert items_before["dup.txt"][0] == KnowledgeBatchItemStatus.EXACT_DUPLICATE
    assert items_before["will-fail.txt"][0] == KnowledgeBatchItemStatus.FAILED

    monkeypatch.setattr(worker_module, "parse_file",
                        lambda path: _FakeParseResult(text="fixed on retry"))
    retry_failed(session, outcome.batch.id, vault_root=vault_root)
    session.flush()
    _run_worker_to_completion(session, monkeypatch, tmp_path)
    finalize_batch_if_terminal(session, outcome.batch.id)

    items_after = {i.archive_member_path_original: i.status
                  for i in session.scalars(select(KnowledgeBatchItem)
                                           .where(KnowledgeBatchItem.batch_id == outcome.batch.id)).all()}
    assert items_after["ok.txt"] == KnowledgeBatchItemStatus.READY
    assert items_after["dup.txt"] == KnowledgeBatchItemStatus.EXACT_DUPLICATE
    assert items_after["will-fail.txt"] == KnowledgeBatchItemStatus.READY  # починилось ретраем


# ── 11. graph status always NOT_APPLICABLE (Graphify не реализован) ────────

def test_graph_status_is_not_applicable_for_every_terminal_item(session, tmp_path, monkeypatch):
    _, outcome, vault_root = _stage_and_resolve(
        session, tmp_path, monkeypatch, {"one.txt": b"a"})
    _run_worker_to_completion(session, monkeypatch, tmp_path)

    items = session.scalars(select(KnowledgeBatchItem)
                            .where(KnowledgeBatchItem.batch_id == outcome.batch.id)).all()
    assert all(i.graph_status == "not_applicable" for i in items)


# ── cancel_remaining / disable_created_sources ──────────────────────────────

def test_cancel_remaining_skips_queued_keeps_completed(session, tmp_path, monkeypatch):
    _, outcome, vault_root = _stage_and_resolve(
        session, tmp_path, monkeypatch, {"done.txt": b"a", "still-queued.txt": b"b"})

    # Обрабатываем только один job вручную, оставляя второй в очереди —
    # эмулирует "владелец отменил, пока часть уже посчитана".
    job = claim_next_job(session)
    monkeypatch.setattr(worker_module, "parse_file", lambda path: _FakeParseResult(text="ok"))
    process_job(session, job)
    session.flush()

    cancel_remaining(session, outcome.batch.id)
    session.flush()

    items = {i.archive_member_path_original: i.status
            for i in session.scalars(select(KnowledgeBatchItem)
                                     .where(KnowledgeBatchItem.batch_id == outcome.batch.id)).all()}
    completed_name = "done.txt" if items["done.txt"] == KnowledgeBatchItemStatus.READY else "still-queued.txt"
    other_name = "still-queued.txt" if completed_name == "done.txt" else "done.txt"
    assert items[completed_name] == KnowledgeBatchItemStatus.READY
    assert items[other_name] == KnowledgeBatchItemStatus.SKIPPED_CANCELLED


def test_disable_created_sources_only_touches_batch_created_not_preexisting(session, tmp_path, monkeypatch):
    from helm_core.knowledge.ingest import register_file_for_ingest
    vault_root = str(tmp_path / "vault")
    pre = tmp_path / "pre.txt"
    pre.write_text("предсуществующий", encoding="utf-8")
    pre_result = register_file_for_ingest(session, domain="engineering", raw_path=pre,
                                          original_filename="pre.txt", vault_root=vault_root)
    session.flush()

    _, outcome, _ = _stage_and_resolve(
        session, tmp_path, monkeypatch,
        {"new.txt": "новый файл".encode("utf-8"), "dup.txt": "предсуществующий".encode("utf-8")})
    _run_worker_to_completion(session, monkeypatch, tmp_path)

    disabled_count = disable_created_sources(session, outcome.batch.id)
    session.flush()

    assert disabled_count == 1  # только new.txt — dup.txt ссылается на pre-existing source
    pre_source = session.get(KnowledgeSource, pre_result.source.id)
    assert pre_source.status == KnowledgeStatus.ACTIVE, "pre-existing source не должен трогаться"
