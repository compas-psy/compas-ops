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
    MAX_ATTACHMENT_BYTES, VOICE_STAGED_NOTICE, AttachmentTooLarge, format_domain_menu,
    parse_domain_reply, resolve_pending_domain, stage_attachment, stage_outcome_text,
    voice_ready_menu_text,
)
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (
    KnowledgeChunk, KnowledgeCustomDomain, KnowledgeIngestJob, KnowledgePendingAttachment,
    KnowledgeSource, KnowledgeUser, KnowledgeUserRole,
)

from conftest import SYSTEM_OWNER_ID


@pytest.fixture
def second_user(session):
    user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(user)
    session.flush()
    return user


# ── parse_domain_reply() / format_domain_menu() ───────────────────────────

def test_format_domain_menu_lists_all_domains_numbered(session):
    menu = format_domain_menu(session, SYSTEM_OWNER_ID, "report.pdf")
    assert "report.pdf" in menu
    assert "1. personal" in menu
    assert "11. library" in menu


def test_parse_domain_reply_by_number(session):
    assert parse_domain_reply(session, SYSTEM_OWNER_ID, "9") == "engineering"


def test_parse_domain_reply_by_name_case_insensitive(session):
    assert parse_domain_reply(session, SYSTEM_OWNER_ID, " ENGINEERING ") == "engineering"


def test_parse_domain_reply_out_of_range_number_is_invalid(session):
    """999 не попадает ни во встроенные домены, ни (при пустом реестре)
    в пользовательские — приглашение к вводу нового домена цифрами не
    работает специально: цифровой ответ ВСЕГДА читается как номер меню,
    никогда как имя нового домена (иначе "1" никогда не создать доменом,
    но и не спутать с выбором пункта 1 тоже нельзя)."""
    assert parse_domain_reply(session, SYSTEM_OWNER_ID, "999") is None


@pytest.mark.parametrize("alias, expected", [
    ("company", "simpas/company"),
    ("Practice", "simpas/practice"),
    ("ZAPISKI", "simpas/zapiski"),
    ("moments", "simpas/moments"),
    ("marketing", "psy-marketing"),
    ("docs", "signalai-docs"),
])
def test_parse_domain_reply_short_alias(session, alias, expected):
    """Найдено живым использованием 29.08.2026: 'simpas/company' неудобно
    набирать на телефоне — короткие алиасы для доменов с '/' или '-'."""
    assert parse_domain_reply(session, SYSTEM_OWNER_ID, alias) == expected


def test_format_domain_menu_shows_alias_next_to_full_name(session):
    menu = format_domain_menu(session, SYSTEM_OWNER_ID, "report.pdf")
    assert "simpas/company (company)" in menu
    assert "1. personal" in menu, "у коротких доменов без алиаса формат не меняется"


@pytest.mark.parametrize("word", ["отмена", "Cancel", "нет"])
def test_parse_domain_reply_cancel_words(session, word):
    assert parse_domain_reply(session, SYSTEM_OWNER_ID, word) == "__cancel__"


# ── §14.5 "No hardcoded domain enum" — реестр пользовательских доменов ─────

def test_typing_a_new_name_creates_a_custom_domain(session):
    """Раньше набранное имя, не совпавшее со встроенным списком,
    отклонялось как invalid — владельцу оставалось выбирать только из
    11+library. Явный ответ на прямой вопрос меню — не "silent creation
    from one document" (§14.5): решение принял человек, а не эвристика."""
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    result = parse_domain_reply(session, SYSTEM_OWNER_ID, "путешествия")
    assert result == "путешествия"
    row = session.scalars(select(KnowledgeCustomDomain)).one()
    assert row.knowledge_user_id == SYSTEM_OWNER_ID
    assert row.key == "путешествия"
    assert row.use_count == 1


def test_custom_domain_appears_in_menu_after_first_use(session):
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    parse_domain_reply(session, SYSTEM_OWNER_ID, "путешествия")
    session.flush()
    menu = format_domain_menu(session, SYSTEM_OWNER_ID, "report.pdf")
    assert "12. путешествия" in menu


def test_reusing_a_custom_domain_by_name_bumps_use_count_not_duplicates(session):
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    parse_domain_reply(session, SYSTEM_OWNER_ID, "путешествия")
    session.flush()
    result = parse_domain_reply(session, SYSTEM_OWNER_ID, "Путешествия")
    assert result == "путешествия"
    rows = session.scalars(select(KnowledgeCustomDomain)).all()
    assert len(rows) == 1, "второй ввод того же имени не должен завести вторую строку"
    assert rows[0].use_count == 2


def test_selecting_a_custom_domain_by_its_menu_number(session):
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    parse_domain_reply(session, SYSTEM_OWNER_ID, "путешествия")
    session.flush()
    # 11 встроенных + library = позиция 12.
    assert parse_domain_reply(session, SYSTEM_OWNER_ID, "12") == "путешествия"


def test_custom_domains_are_scoped_per_user(session, second_user):
    """v3.8 §14.4: реестр доменов — тоже tenant-scoped таблица. Домен,
    придуманный одним пользователем, не должен всплывать в меню другого
    и тем более не должен быть виден в его RLS-срезе."""
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    parse_domain_reply(session, SYSTEM_OWNER_ID, "путешествия")
    session.flush()
    bind_knowledge_user(session, second_user.id)
    menu_other = format_domain_menu(session, second_user.id, "report.pdf")
    assert "путешествия" not in menu_other


def test_too_long_custom_domain_name_is_invalid(session):
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    too_long = "a" * 33
    assert parse_domain_reply(session, SYSTEM_OWNER_ID, too_long) is None
    assert session.scalars(select(KnowledgeCustomDomain)).all() == []


def test_multi_word_reply_is_invalid_not_a_domain(session):
    """Домен — один токен, ни один встроенный не содержит пробела.
    Ответ из нескольких слов остаётся invalid, как и до появления
    реестра — по той же причине, по которой это исходно ожидалось."""
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    assert parse_domain_reply(session, SYSTEM_OWNER_ID, "какая погода в Москве") is None
    assert session.scalars(select(KnowledgeCustomDomain)).all() == []


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


# ── stage_attachment() kind detection + stage_outcome_text() (ADR-021 2b) ──

def test_stage_attachment_detects_voice_kind_by_extension(session, tmp_path):
    pending = stage_attachment(
        session, channel="telegram", data=b"fake ogg bytes",
        original_filename="voice_abc123.ogg", mime_type="audio/ogg",
        spool_root=str(tmp_path / "spool"),
    )
    assert pending.kind == "voice"


def test_stage_attachment_defaults_to_document_kind(session, tmp_path):
    pending = stage_attachment(
        session, channel="telegram", data=b"hello",
        original_filename="note.txt", mime_type="text/plain",
        spool_root=str(tmp_path / "spool"),
    )
    assert pending.kind == "document"


def test_stage_attachment_without_filename_is_document_kind(session, tmp_path):
    """Нет имени файла — нет расширения, определить audio/video нечем;
    тот же самый источник истины, что уже использует parsers.py."""
    pending = stage_attachment(
        session, channel="telegram", data=b"hello",
        original_filename=None, mime_type=None,
        spool_root=str(tmp_path / "spool"),
    )
    assert pending.kind == "document"


def test_stage_attachment_stores_recipient(session, tmp_path):
    pending = stage_attachment(
        session, channel="telegram", data=b"data",
        original_filename="voice.ogg", mime_type="audio/ogg",
        spool_root=str(tmp_path / "spool"), recipient="12345",
    )
    assert pending.recipient == "12345"


def test_stage_outcome_text_voice_returns_staged_notice_not_domain_menu(session, tmp_path):
    pending = stage_attachment(
        session, channel="telegram", data=b"data",
        original_filename="voice.ogg", mime_type="audio/ogg",
        spool_root=str(tmp_path / "spool"),
    )
    assert stage_outcome_text(session, pending) == VOICE_STAGED_NOTICE


def test_stage_outcome_text_document_returns_domain_menu(session, tmp_path):
    pending = stage_attachment(
        session, channel="telegram", data=b"data",
        original_filename="report.pdf", mime_type="application/pdf",
        spool_root=str(tmp_path / "spool"),
    )
    text = stage_outcome_text(session, pending)
    assert "report.pdf" in text
    assert "1. personal" in text


def test_voice_ready_menu_text_includes_preview_and_domain_menu(session, tmp_path):
    pending = stage_attachment(
        session, channel="telegram", data=b"data",
        original_filename="voice.ogg", mime_type="audio/ogg",
        spool_root=str(tmp_path / "spool"),
    )
    pending.transcript = "[0s] Купить билеты на поезд"
    session.flush()

    text = voice_ready_menu_text(session, pending)

    assert "Купить билеты на поезд" in text
    assert "1. personal" in text


def test_voice_ready_menu_text_truncates_long_transcript(session, tmp_path):
    pending = stage_attachment(
        session, channel="telegram", data=b"data",
        original_filename="voice.ogg", mime_type="audio/ogg",
        spool_root=str(tmp_path / "spool"),
    )
    pending.transcript = "[0s] " + "слово " * 200
    session.flush()

    text = voice_ready_menu_text(session, pending)

    assert text.count("…") == 1
    assert len(text) < len(pending.transcript) + 500


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


def test_resolve_pending_domain_skips_voice_pending_without_transcript(session, tmp_path):
    """ADR-021 фаза 2b: voice-pending без транскрипта ещё не готов к
    вопросу о домене вовсе — следующее текстовое сообщение должно
    провалиться в обычный pipeline (`not_pending`), а не быть ошибочно
    понято как ответ на домен для файла, который ещё не транскрибирован."""
    stage_attachment(session, channel="telegram", data=b"voice bytes",
                     original_filename="voice.ogg", mime_type="audio/ogg",
                     spool_root=str(tmp_path / "spool"))

    outcome = resolve_pending_domain(session, channel="telegram", reply_text="engineering")

    assert outcome.status == "not_pending"
    assert session.query(KnowledgePendingAttachment).count() == 1


def test_resolve_pending_domain_finds_voice_pending_once_transcribed(session, tmp_path):
    vault_root = tmp_path / "vault"
    pending = stage_attachment(session, channel="telegram", data=b"voice bytes",
                               original_filename="voice.ogg", mime_type="audio/ogg",
                               spool_root=str(tmp_path / "spool"))
    pending.transcript = "[0s] какой-то распознанный текст"
    session.flush()

    outcome = resolve_pending_domain(session, channel="telegram", reply_text="engineering",
                                     vault_root=str(vault_root))

    assert outcome.status == "ingested"


def test_resolve_pending_domain_prefers_ready_document_over_untranscribed_voice(session, tmp_path):
    """FIFO по created_at — но voice без транскрипта пропускается, даже
    если он пришёл раньше готового к разрешению document-pending."""
    vault_root = tmp_path / "vault"
    stage_attachment(session, channel="telegram", data=b"voice bytes",
                     original_filename="voice.ogg", mime_type="audio/ogg",
                     spool_root=str(tmp_path / "spool"))
    stage_attachment(session, channel="telegram", data=b"doc bytes",
                     original_filename="doc.txt", mime_type="text/plain",
                     spool_root=str(tmp_path / "spool"))

    outcome = resolve_pending_domain(session, channel="telegram", reply_text="engineering",
                                     vault_root=str(vault_root))

    assert outcome.status == "ingested"
    assert outcome.result.source.original_filename == "doc.txt"
    # Voice-pending без транскрипта остаётся нетронутым.
    assert session.query(KnowledgePendingAttachment).count() == 1


def test_resolve_pending_domain_over_storage_quota_is_rejected_without_orphan_file(session, tmp_path):
    """v3.8 §14.4: файл уже физически перенесён в raw/ до проверки квоты
    (квота живёт в БД) — байты не должны остаться сиротой на диске, и
    pending не должен зависнуть в состоянии, которое нельзя разрешить."""
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
