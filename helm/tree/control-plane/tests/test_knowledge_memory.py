"""P8.5.12 Micro-Memory «Запомни» (v3.8 §14.10-14.11)."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from helm_core.knowledge.memory import (
    FORBIDDEN_SECRET_NOTICE, MICRO_MEMORY_MAX_CHARS, classify_kind, compute_dedup_hash,
    detect_remember_command, extract_url, is_forbidden_secret, parse_temporal_expiry,
    try_remember,
)
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (
    KnowledgeChunk, KnowledgeIngestJob, KnowledgeMemory, KnowledgeMemoryStatus, KnowledgeSource,
    KnowledgeUser, KnowledgeUserRole,
)

from conftest import SYSTEM_OWNER_ID


@pytest.fixture
def second_user(session):
    user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(user)
    session.flush()
    return user


# ── detect_remember_command() ───────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Запомни ссылку про титры: https://example.com/x", "ссылку про титры: https://example.com/x"),
    ("запомни купить молоко", "купить молоко"),
    ("/remember buy milk", "buy milk"),
    ("Сохрани в память: адрес парковки", "адрес парковки"),
    ("Не забудь: позвонить маме", "позвонить маме"),
    ("не забудьте выключить свет", "выключить свет"),
])
def test_detect_remember_command_strips_known_prefixes(text, expected):
    assert detect_remember_command(text) == expected


@pytest.mark.parametrize("text", [
    "какое решение приняли по проекту",
    "собери отчёт",
    "",
])
def test_detect_remember_command_returns_none_for_non_commands(text):
    assert detect_remember_command(text) is None


def test_detect_remember_command_returns_none_for_empty_payload():
    assert detect_remember_command("Запомни") is None
    assert detect_remember_command("Запомни   ") is None


# ── is_forbidden_secret() ────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "пароль от почты: hunter2",
    "мой password: hunter2",
    "CVV2 карты: 123",
    "код подтверждения: 4821",
    "вот api key для сервиса",
    "seed phrase от кошелька: ...",
])
def test_is_forbidden_secret_detects_labeled_secrets(text):
    assert is_forbidden_secret(text) is True


@pytest.mark.parametrize("text", [
    "купить молоко и хлеб",
    "номер машины курьера А123ВС77",
    "ссылка на видео про титры",
])
def test_is_forbidden_secret_allows_ordinary_text(text):
    assert is_forbidden_secret(text) is False


# ── classify_kind()/extract_url() ───────────────────────────────────────────

def test_classify_kind_bookmark_for_bare_url():
    assert classify_kind("https://example.com/titles") == "bookmark"
    assert classify_kind("https://example.com/titles про титры") == "bookmark"


def test_classify_kind_note_for_prose_mentioning_a_url():
    text = "почитать эту статью позже, там хорошо расписано: https://example.com/x, важно для проекта"
    assert classify_kind(text) == "note"


def test_classify_kind_note_for_plain_text():
    assert classify_kind("номер машины курьера А123ВС77") == "note"


def test_extract_url_returns_none_without_url():
    assert extract_url("просто текст") is None


# ── compute_dedup_hash() ─────────────────────────────────────────────────────

def test_compute_dedup_hash_ignores_case_and_whitespace():
    a = compute_dedup_hash("Купить  молоко")
    b = compute_dedup_hash("купить молоко")
    assert a == b


def test_compute_dedup_hash_differs_for_different_text():
    assert compute_dedup_hash("а") != compute_dedup_hash("б")


# ── parse_temporal_expiry() ──────────────────────────────────────────────────

def test_parse_temporal_expiry_today_is_end_of_local_day():
    now = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    expires = parse_temporal_expiry("курьер сегодня", timezone_name="Europe/Moscow", now=now)
    local = expires.astimezone(ZoneInfo("Europe/Moscow"))
    assert local.date().isoformat() == "2026-08-30"
    assert local.hour == 23 and local.minute == 59


def test_parse_temporal_expiry_tomorrow_is_end_of_next_local_day():
    now = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    expires = parse_temporal_expiry("встреча завтра", timezone_name="Europe/Moscow", now=now)
    local = expires.astimezone(ZoneInfo("Europe/Moscow"))
    assert local.date().isoformat() == "2026-08-31"


def test_parse_temporal_expiry_none_without_explicit_cue():
    now = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    assert parse_temporal_expiry("номер машины курьера", timezone_name="Europe/Moscow", now=now) is None


# ── try_remember(): полный путь ──────────────────────────────────────────────

def test_try_remember_not_a_command_returns_early(session):
    outcome = try_remember(session, channel="max", text="собери отчёт по проекту")
    assert outcome.status == "not_command"
    assert session.scalars(select(KnowledgeMemory)).all() == []


def test_try_remember_rejects_forbidden_secret_without_writing_anything(session):
    outcome = try_remember(session, channel="max", text="Запомни пароль от почты: hunter2")
    assert outcome.status == "rejected_secret"
    assert outcome.text == FORBIDDEN_SECRET_NOTICE
    assert session.scalars(select(KnowledgeMemory)).all() == []


def test_try_remember_stores_fact_with_confirmation(session, tmp_path):
    outcome = try_remember(session, channel="max",
                           text="Запомни номер машины курьера: А123ВС77",
                           vault_root=str(tmp_path))

    assert outcome.status == "stored"
    memory = outcome.memory
    assert memory.knowledge_user_id == SYSTEM_OWNER_ID
    assert memory.kind == "note"
    assert memory.canonical_text == "номер машины курьера: А123ВС77"
    assert memory.status == KnowledgeMemoryStatus.ACTIVE
    assert "А123ВС77" in outcome.text
    mirror = tmp_path / "users" / str(SYSTEM_OWNER_ID) / "memory" / f"{memory.id}.md"
    assert mirror.exists()
    assert "А123ВС77" in mirror.read_text(encoding="utf-8")
    # §14.10 "normal Micro-Memory does not run document parser/chunker":
    # обычная память — прямой FTS-юнит, а не документ. Ни источника, ни
    # чанков, ни job'а разбора появиться не должно.
    assert session.scalars(select(KnowledgeSource)).all() == []
    assert session.scalars(select(KnowledgeChunk)).all() == []
    assert session.scalars(select(KnowledgeIngestJob)).all() == []


def test_try_remember_stores_bookmark(session, tmp_path):
    outcome = try_remember(session, channel="max",
                           text="Запомни ссылку про титры: https://example.com/titles",
                           vault_root=str(tmp_path))

    assert outcome.status == "stored"
    assert outcome.memory.kind == "bookmark"
    assert outcome.memory.payload_json == {"url": "https://example.com/titles"}
    assert "https://example.com/titles" in outcome.text


def test_try_remember_exact_repeat_is_deduped(session, tmp_path):
    first = try_remember(session, channel="max", text="Запомни купить молоко",
                         vault_root=str(tmp_path))
    second = try_remember(session, channel="max", text="запомни купить молоко",
                          vault_root=str(tmp_path))

    assert first.status == "stored"
    assert second.status == "duplicate"
    assert second.memory.id == first.memory.id
    assert len(session.scalars(select(KnowledgeMemory)).all()) == 1


def test_try_remember_routes_overflow_text_to_source_not_memory(session, tmp_path):
    long_text = "Запомни " + ("многабукв " * (MICRO_MEMORY_MAX_CHARS // 10 + 10))

    outcome = try_remember(session, channel="max", text=long_text, vault_root=str(tmp_path))

    assert outcome.status == "stored_as_source"
    assert isinstance(outcome.source, KnowledgeSource)
    assert session.scalars(select(KnowledgeMemory)).all() == []


def test_try_remember_does_not_dedup_or_leak_across_users(session, second_user, tmp_path):
    owner_outcome = try_remember(session, channel="max", text="Запомни один и тот же текст",
                                 vault_root=str(tmp_path))
    other_outcome = try_remember(session, channel="max", text="Запомни один и тот же текст",
                                 knowledge_user_id=second_user.id, vault_root=str(tmp_path))

    assert owner_outcome.status == "stored"
    assert other_outcome.status == "stored"
    assert owner_outcome.memory.id != other_outcome.memory.id

    bind_knowledge_user(session, second_user.id)
    session.expunge_all()
    assert session.get(KnowledgeMemory, owner_outcome.memory.id) is None
    assert session.get(KnowledgeMemory, other_outcome.memory.id) is not None
