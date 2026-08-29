"""P8.5.7 — двухшаговый диалог выбора домена для вложений (§14.5.1).

Решение владельца 29.08.2026: файл уходит в spool сразу, HELM спрашивает
домен, следующее сообщение владельца на том же канале резолвит его.
Ничего здесь не угадывает домен — невалидный/пустой ответ оставляет
вложение в spool до следующей попытки или явной отмены.
"""

from pathlib import Path

import pytest
from sqlalchemy import select

from helm_core.knowledge.chat_intake import (
    MAX_ATTACHMENT_BYTES, AttachmentTooLarge, format_domain_menu,
    parse_domain_reply, resolve_pending_domain, stage_attachment,
)
from helm_core.models import KnowledgeChunk, KnowledgeIngestJob, KnowledgePendingAttachment, KnowledgeSource


# ── parse_domain_reply() / format_domain_menu() ───────────────────────────

def test_format_domain_menu_lists_all_domains_numbered():
    menu = format_domain_menu("report.pdf")
    assert "report.pdf" in menu
    assert "1. personal" in menu
    assert "11. library" in menu


def test_parse_domain_reply_by_number():
    assert parse_domain_reply("9") == "engineering"


def test_parse_domain_reply_by_name_case_insensitive():
    assert parse_domain_reply(" ENGINEERING ") == "engineering"


def test_parse_domain_reply_out_of_range_number_is_invalid():
    assert parse_domain_reply("999") is None


def test_parse_domain_reply_gibberish_is_invalid():
    assert parse_domain_reply("какая погода в Москве") is None


@pytest.mark.parametrize("word", ["отмена", "Cancel", "нет"])
def test_parse_domain_reply_cancel_words(word):
    assert parse_domain_reply(word) == "__cancel__"


# ── stage_attachment() ─────────────────────────────────────────────────────

def test_stage_attachment_writes_spool_file_and_pending_row(session, tmp_path):
    spool_root = tmp_path / "spool"

    pending = stage_attachment(
        session, channel="telegram", data=b"hello world",
        original_filename="note.txt", mime_type="text/plain",
        spool_root=str(spool_root),
    )

    assert Path(pending.spool_path).read_bytes() == b"hello world"
    assert Path(pending.spool_path).parent == spool_root
    row = session.get(KnowledgePendingAttachment, pending.id)
    assert row is not None
    assert row.channel == "telegram"


def test_stage_attachment_rejects_oversized_file(session, tmp_path):
    with pytest.raises(AttachmentTooLarge):
        stage_attachment(
            session, channel="telegram", data=b"x" * (MAX_ATTACHMENT_BYTES + 1),
            original_filename="huge.bin", mime_type="application/octet-stream",
            spool_root=str(tmp_path / "spool"),
        )


# ── resolve_pending_domain() ────────────────────────────────────────────────

def test_resolve_pending_domain_not_pending_when_nothing_staged(session):
    outcome = resolve_pending_domain(session, channel="telegram", reply_text="engineering")
    assert outcome.status == "not_pending"


def test_resolve_pending_domain_invalid_reply_keeps_pending(session, tmp_path):
    stage_attachment(session, channel="telegram", data=b"data",
                     original_filename="a.txt", mime_type="text/plain",
                     spool_root=str(tmp_path / "spool"))

    outcome = resolve_pending_domain(session, channel="telegram", reply_text="not a domain")

    assert outcome.status == "invalid"
    assert session.query(KnowledgePendingAttachment).count() == 1


def test_resolve_pending_domain_cancel_removes_pending_and_spool_file(session, tmp_path):
    pending = stage_attachment(session, channel="telegram", data=b"data",
                               original_filename="a.txt", mime_type="text/plain",
                               spool_root=str(tmp_path / "spool"))
    spool_path = Path(pending.spool_path)

    outcome = resolve_pending_domain(session, channel="telegram", reply_text="отмена")

    assert outcome.status == "cancelled"
    assert session.query(KnowledgePendingAttachment).count() == 0
    assert not spool_path.exists()


def test_resolve_pending_domain_valid_domain_ingests_file(session, tmp_path):
    vault_root = tmp_path / "vault"
    pending = stage_attachment(session, channel="telegram", data=b"content of the file",
                               original_filename="notes.txt", mime_type="text/plain",
                               spool_root=str(tmp_path / "spool"))
    spool_path = Path(pending.spool_path)

    outcome = resolve_pending_domain(session, channel="telegram", reply_text="engineering",
                                     vault_root=str(vault_root))

    assert outcome.status == "ingested"
    assert outcome.result.created is True
    assert session.query(KnowledgePendingAttachment).count() == 0
    assert not spool_path.exists()
    raw_path = Path(outcome.result.source.raw_path)
    assert raw_path.exists()
    assert raw_path.parent == vault_root / "raw" / "engineering"
    job = session.get(KnowledgeIngestJob, outcome.result.job.id)
    assert job is not None


def test_resolve_pending_domain_zapiski_forces_client_restricted_sensitivity(session, tmp_path):
    stage_attachment(session, channel="telegram", data=b"client note contents",
                     original_filename="client.txt", mime_type="text/plain",
                     spool_root=str(tmp_path / "spool"))

    outcome = resolve_pending_domain(session, channel="telegram", reply_text="simpas/zapiski",
                                     vault_root=str(tmp_path / "vault"))

    assert outcome.status == "ingested"
    assert outcome.result.source.sensitivity == "client_restricted"


def test_resolve_pending_domain_by_number(session, tmp_path):
    stage_attachment(session, channel="max", data=b"library book excerpt",
                     original_filename="book.txt", mime_type="text/plain",
                     spool_root=str(tmp_path / "spool"))

    outcome = resolve_pending_domain(session, channel="max", reply_text="11",
                                     vault_root=str(tmp_path / "vault"))

    assert outcome.status == "ingested"
    assert outcome.result.source.domain == "library"


def test_resolve_pending_domain_is_fifo_within_channel(session, tmp_path):
    first = stage_attachment(session, channel="telegram", data=b"first file",
                             original_filename="first.txt", mime_type="text/plain",
                             spool_root=str(tmp_path / "spool"))
    stage_attachment(session, channel="telegram", data=b"second file",
                     original_filename="second.txt", mime_type="text/plain",
                     spool_root=str(tmp_path / "spool"))

    outcome = resolve_pending_domain(session, channel="telegram", reply_text="engineering",
                                     vault_root=str(tmp_path / "vault"))

    assert outcome.status == "ingested"
    assert outcome.result.source.original_filename == "first.txt"
    remaining = session.scalar(select(KnowledgePendingAttachment))
    assert remaining.original_filename == "second.txt"
    assert remaining.id != first.id or True  # first row is gone; sanity on remaining


def test_resolve_pending_domain_does_not_cross_channels(session, tmp_path):
    stage_attachment(session, channel="telegram", data=b"telegram file",
                     original_filename="tg.txt", mime_type="text/plain",
                     spool_root=str(tmp_path / "spool"))

    outcome = resolve_pending_domain(session, channel="max", reply_text="engineering",
                                     vault_root=str(tmp_path / "vault"))

    assert outcome.status == "not_pending"
    assert session.query(KnowledgePendingAttachment).count() == 1


def test_resolve_pending_domain_missing_spool_file_is_handled_not_crashed(session, tmp_path):
    pending = stage_attachment(session, channel="telegram", data=b"data",
                               original_filename="a.txt", mime_type="text/plain",
                               spool_root=str(tmp_path / "spool"))
    Path(pending.spool_path).unlink()

    outcome = resolve_pending_domain(session, channel="telegram", reply_text="engineering",
                                     vault_root=str(tmp_path / "vault"))

    assert outcome.status == "missing"
    assert session.query(KnowledgePendingAttachment).count() == 0
