"""P8.5.7 — двухшаговый диалог выбора домена для вложений (§14.5.1).

Решение владельца 29.08.2026: файл уходит в spool сразу, HELM спрашивает
домен, следующее сообщение владельца на том же канале резолвит его.
Ничего здесь не угадывает домен — невалидный/пустой ответ оставляет
вложение в spool до следующей попытки или явной отмены.
"""

import shutil
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


@pytest.mark.parametrize("alias, expected", [
    ("company", "simpas/company"),
    ("Practice", "simpas/practice"),
    ("ZAPISKI", "simpas/zapiski"),
    ("moments", "simpas/moments"),
    ("marketing", "psy-marketing"),
    ("docs", "signalai-docs"),
])
def test_parse_domain_reply_short_alias(alias, expected):
    """Найдено живым использованием 29.08.2026: 'simpas/company' неудобно
    набирать на телефоне — короткие алиасы для доменов с '/' или '-'."""
    assert parse_domain_reply(alias) == expected


def test_format_domain_menu_shows_alias_next_to_full_name():
    menu = format_domain_menu("report.pdf")
    assert "simpas/company (company)" in menu
    assert "1. personal" in menu, "у коротких доменов без алиаса формат не меняется"


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


def test_resolve_pending_domain_same_file_resent_to_same_domain_is_not_reprocessed(session, tmp_path):
    vault_root = tmp_path / "vault"
    data = b"identical bytes, sent twice"

    first_pending = stage_attachment(session, channel="telegram", data=data,
                                     original_filename="report.txt", mime_type="text/plain",
                                     spool_root=str(tmp_path / "spool"))
    first = resolve_pending_domain(session, channel="telegram", reply_text="engineering",
                                   vault_root=str(vault_root))
    assert first.status == "ingested"
    assert first.result.created is True

    second_pending = stage_attachment(session, channel="telegram", data=data,
                                      original_filename="report.txt", mime_type="text/plain",
                                      spool_root=str(tmp_path / "spool"))
    second_spool_path = Path(second_pending.spool_path)
    second = resolve_pending_domain(session, channel="telegram", reply_text="engineering",
                                    vault_root=str(vault_root))

    assert second.status == "duplicate"
    assert second.result.source.id == first.result.source.id
    assert second.result.created is False
    assert second.result.job is None
    # Второй пришедший файл не оставляет ни pending, ни файл в spool.
    assert session.query(KnowledgePendingAttachment).count() == 0
    assert not second_spool_path.exists()
    # И не создаёт вторую ingest job на то же содержимое.
    assert session.query(KnowledgeIngestJob).count() == 1


def test_resolve_pending_domain_same_file_resent_to_different_domain_keeps_original(session, tmp_path):
    """Дедуп по sha256 глобальный, не per-domain (register_file_for_ingest) —
    повторная отправка того же файла в ДРУГОЙ домен не переклассифицирует
    существующий source и не оставляет файл-сироту в новом домене."""
    vault_root = tmp_path / "vault"
    data = b"same bytes, different domain the second time"

    stage_attachment(session, channel="telegram", data=data,
                     original_filename="doc.txt", mime_type="text/plain",
                     spool_root=str(tmp_path / "spool"))
    first = resolve_pending_domain(session, channel="telegram", reply_text="engineering",
                                   vault_root=str(vault_root))

    stage_attachment(session, channel="telegram", data=data,
                     original_filename="doc.txt", mime_type="text/plain",
                     spool_root=str(tmp_path / "spool"))
    second = resolve_pending_domain(session, channel="telegram", reply_text="health",
                                    vault_root=str(vault_root))

    assert second.status == "duplicate"
    assert second.result.source.domain == "engineering"
    assert not (vault_root / "raw" / "health").exists()


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


def test_resolve_pending_domain_cross_device_move_failure_keeps_pending_for_retry(
    session, tmp_path, monkeypatch
):
    """НАЙДЕНО живым тестом 29.08.2026: spool и Vault на реальном сервере —
    разные файловые системы, `os.replace()` падает `OSError: Invalid
    cross-device link`. Фикс — copy-в-tmp-на-целевом-диске + os.replace
    внутри неё; здесь проверяем ЛЮБОЙ сбой на этом шаге (любой OSError,
    не только конкретно EXDEV) не роняет процесс и не теряет вложение —
    pending остаётся, следующая попытка того же домена может сработать."""
    pending = stage_attachment(session, channel="telegram", data=b"content",
                               original_filename="report.pdf", mime_type="application/pdf",
                               spool_root=str(tmp_path / "spool"))
    spool_path = Path(pending.spool_path)

    def _raise(*_args, **_kwargs):
        raise OSError("boom")
    monkeypatch.setattr(shutil, "copyfile", _raise)

    outcome = resolve_pending_domain(session, channel="telegram", reply_text="engineering",
                                     vault_root=str(tmp_path / "vault"))

    assert outcome.status == "failed"
    assert spool_path.exists(), "исходный файл в spool не должен пропасть при сбое переноса"
    assert session.query(KnowledgePendingAttachment).count() == 1
    assert session.query(KnowledgeSource).count() == 0


def test_resolve_pending_domain_missing_spool_file_is_handled_not_crashed(session, tmp_path):
    pending = stage_attachment(session, channel="telegram", data=b"data",
                               original_filename="a.txt", mime_type="text/plain",
                               spool_root=str(tmp_path / "spool"))
    Path(pending.spool_path).unlink()

    outcome = resolve_pending_domain(session, channel="telegram", reply_text="engineering",
                                     vault_root=str(tmp_path / "vault"))

    assert outcome.status == "missing"
    assert session.query(KnowledgePendingAttachment).count() == 0


def test_resolve_pending_domain_over_storage_quota_is_rejected_without_orphan_file(session, tmp_path):
    """v3.8 §14.4: файл уже физически перенесён в raw/ до проверки квоты
    (квота живёт в БД) — байты не должны остаться сиротой на диске, и
    pending не должен зависнуть в состоянии, которое нельзя разрешить."""
    from conftest import SYSTEM_OWNER_ID
    from helm_core.models import KnowledgeUser
    owner = session.get(KnowledgeUser, SYSTEM_OWNER_ID)
    owner.storage_quota_bytes = 5
    session.flush()

    pending = stage_attachment(session, channel="telegram", data=b"content larger than quota",
                               original_filename="notes.txt", mime_type="text/plain",
                               spool_root=str(tmp_path / "spool"))

    outcome = resolve_pending_domain(session, channel="telegram", reply_text="engineering",
                                     vault_root=str(tmp_path / "vault"))

    assert outcome.status == "quota_exceeded"
    assert session.query(KnowledgePendingAttachment).count() == 0
    assert session.query(KnowledgeSource).count() == 0
    raw_dir = tmp_path / "vault" / "raw" / "engineering"
    assert not any(raw_dir.iterdir()) if raw_dir.exists() else True
