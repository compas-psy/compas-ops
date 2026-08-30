"""v3.8 §14.3 — выгрузка своего архива и окончательное удаление.

Порядок из спеки: suspend → optional export → explicit RED delete.
Каждый шаг отдельный; проверяется в том числе, что перепрыгнуть шаг
нельзя.
"""

import json
import re
import uuid
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.memory import try_remember
from helm_core.knowledge.offboarding import (
    BACKUP_KEEP_DAILY, BACKUP_KEEP_MONTHLY, BACKUP_KEEP_WEEKLY, BACKUP_RETENTION_NOTICE,
    DeleteRefused, delete_user_permanently, export_user_vault, user_vault_dir,
)
from helm_core.knowledge.onboarding import suspend_user
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (
    KnowledgeChunk, KnowledgeMemory, KnowledgeSource, KnowledgeUser, KnowledgeUserRole,
    KnowledgeUserStatus, KnowledgeUserUsage,
)

#: tests/ → control-plane/ → tree/ → scripts/
BACKUP_SH = Path(__file__).resolve().parents[2] / "scripts" / "backup.sh"


@pytest.fixture
def secondary(session):
    user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER,
                         status=KnowledgeUserStatus.ACTIVE, display_name="Аня")
    session.add(user)
    session.flush()
    return user


def _fill(session, user_id, vault_root):
    try_remember(session, channel="telegram_knowledge", text="Запомни: код домофона 4512",
                 knowledge_user_id=user_id, vault_root=str(vault_root))
    session.flush()
    ingest_text(session, domain="personal", text="Мой документ про кота.",
                original_filename="cat.md", knowledge_user_id=user_id)
    session.flush()


# ── сроки хранения ───────────────────────────────────────────────────────

def test_retention_numbers_match_the_actual_backup_script():
    """Названный человеку срок обязан совпадать с реальной политикой
    restic. Расхождение здесь — это не рассинхрон констант, а неверное
    обещание живому человеку про его данные."""
    script = BACKUP_SH.read_text(encoding="utf-8")
    forget = re.search(r"restic forget\s+(.+)", script).group(1)

    assert f"--keep-daily {BACKUP_KEEP_DAILY}" in forget
    assert f"--keep-weekly {BACKUP_KEEP_WEEKLY}" in forget
    assert f"--keep-monthly {BACKUP_KEEP_MONTHLY}" in forget


def test_retention_notice_states_the_upper_bound():
    assert str(BACKUP_KEEP_MONTHLY) in BACKUP_RETENTION_NOTICE
    assert "резервных копиях" in BACKUP_RETENTION_NOTICE


# ── выгрузка ─────────────────────────────────────────────────────────────

def test_export_contains_own_memories_and_sources(session, secondary, tmp_path):
    vault = tmp_path / "vault"
    _fill(session, secondary.id, vault)

    result = export_user_vault(session, secondary.id, out_dir=tmp_path / "out",
                               vault_root=str(vault))

    assert result.memories == 1 and result.sources == 1
    with zipfile.ZipFile(result.archive_path) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
    assert "manifest.json" in names and "sources.json" in names
    assert any(n.startswith("memory/") and n.endswith(".md") for n in names)
    assert manifest["memories"][0]["text"] == "код домофона 4512"
    assert manifest["display_name"] == "Аня"


def test_export_never_contains_another_users_data(session, secondary, tmp_path):
    vault = tmp_path / "vault"
    owner_id = bind_knowledge_user(session, None)
    try_remember(session, channel="max", text="Запомни: секрет владельца 9999",
                 knowledge_user_id=owner_id, vault_root=str(vault))
    session.flush()
    _fill(session, secondary.id, vault)

    result = export_user_vault(session, secondary.id, out_dir=tmp_path / "out",
                               vault_root=str(vault))

    with zipfile.ZipFile(result.archive_path) as archive:
        blob = b"".join(archive.read(n) for n in archive.namelist())
    assert "9999".encode("utf-8") not in blob


def test_export_states_that_originals_are_not_included(session, secondary, tmp_path):
    """Человек не должен решить, что получил всё, если оригиналов там нет."""
    vault = tmp_path / "vault"
    _fill(session, secondary.id, vault)

    result = export_user_vault(session, secondary.id, out_dir=tmp_path / "out",
                               vault_root=str(vault))

    with zipfile.ZipFile(result.archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        sources = json.loads(archive.read("sources.json"))
    assert manifest["raw_originals_included"] is False
    assert manifest["raw_originals_note"]
    # Но перечень полный, с хэшами — по нему файл можно затребовать.
    assert sources[0]["sha256"] and sources[0]["original_filename"] == "cat.md"


def test_export_discloses_backup_retention(session, secondary, tmp_path):
    result = export_user_vault(session, secondary.id, out_dir=tmp_path / "out",
                               vault_root=str(tmp_path / "vault"))
    with zipfile.ZipFile(result.archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["backup_retention"] == BACKUP_RETENTION_NOTICE


# ── удаление ─────────────────────────────────────────────────────────────

def test_delete_requires_suspension_first(session, secondary, tmp_path):
    """§14.3 задаёт порядок suspend → export → delete. Требование
    приостановки заранее делает удаление двумя разнесёнными решениями."""
    with pytest.raises(DeleteRefused, match="приостановите"):
        delete_user_permanently(session, secondary.id, vault_root=str(tmp_path))


def test_delete_removes_content_rows_and_files(session, secondary, tmp_path):
    vault = tmp_path / "vault"
    _fill(session, secondary.id, vault)
    assert user_vault_dir(vault, secondary.id).is_dir()
    suspend_user(session, secondary.id)
    session.flush()

    result = delete_user_permanently(session, secondary.id, vault_root=str(vault))
    session.flush()

    assert result.rows_deleted > 0
    assert result.files_removed is True
    assert not user_vault_dir(vault, secondary.id).exists()

    bind_knowledge_user(session, secondary.id)
    assert session.scalars(select(KnowledgeMemory)).all() == []
    assert session.scalars(select(KnowledgeSource)).all() == []
    assert session.scalars(select(KnowledgeChunk)).all() == []
    assert session.get(KnowledgeUserUsage, secondary.id) is None


def test_delete_leaves_a_tombstone_not_a_hole(session, secondary, tmp_path):
    """Строка пользователя остаётся с пометкой DELETED: она держит внешние
    ключи, отвечает «кто и когда был удалён» и не даёт переиспользовать
    тот же идентификатор."""
    suspend_user(session, secondary.id)
    session.flush()

    delete_user_permanently(session, secondary.id, vault_root=str(tmp_path))
    session.flush()

    tombstone = session.get(KnowledgeUser, secondary.id)
    assert tombstone is not None
    assert tombstone.status == KnowledgeUserStatus.DELETED
    assert tombstone.display_name is None


def test_delete_does_not_touch_another_user(session, secondary, tmp_path):
    vault = tmp_path / "vault"
    owner_id = bind_knowledge_user(session, None)
    try_remember(session, channel="max", text="Запомни: заметка владельца",
                 knowledge_user_id=owner_id, vault_root=str(vault))
    session.flush()
    _fill(session, secondary.id, vault)
    suspend_user(session, secondary.id)
    session.flush()

    delete_user_permanently(session, secondary.id, vault_root=str(vault))
    session.flush()

    bind_knowledge_user(session, owner_id)
    remaining = session.scalars(select(KnowledgeMemory)).all()
    assert [m.canonical_text for m in remaining] == ["заметка владельца"]
    assert user_vault_dir(vault, owner_id).is_dir()


def test_delete_is_not_repeatable(session, secondary, tmp_path):
    suspend_user(session, secondary.id)
    session.flush()
    delete_user_permanently(session, secondary.id, vault_root=str(tmp_path))
    session.flush()

    with pytest.raises(DeleteRefused, match="уже удалён"):
        delete_user_permanently(session, secondary.id, vault_root=str(tmp_path))


def test_delete_unknown_user_is_refused(session, tmp_path):
    with pytest.raises(DeleteRefused, match="не найден"):
        delete_user_permanently(session, uuid.uuid4(), vault_root=str(tmp_path))
