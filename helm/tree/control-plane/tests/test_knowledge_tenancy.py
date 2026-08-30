"""v3.8 §14.2/§14.4 — схема тенантности: resolve_system_owner_id() и
per-tenant дедуп (не глобальный SHA256, как до v3.8).

Самый важный сценарий здесь — "same SHA in User A and B yields NO
duplicate signal" (явный acceptance-критерий CONTINUE_HELM_v3.7_TO_v3.8.md):
идентичные байты от двух разных knowledge_user_id обязаны стать двумя
разными `KnowledgeSource` строками, не одной с "первый владелец выиграл".
"""

import uuid

import pytest
import sqlalchemy.exc
from sqlalchemy import select, text

from helm_core.knowledge.batch_intake import stage_batch
from helm_core.knowledge.chat_intake import stage_attachment
from helm_core.knowledge.ingest import ingest_text, register_file_for_ingest
from helm_core.knowledge.tenancy import bind_knowledge_user, resolve_system_owner_id
from helm_core.models import KnowledgeIngestBatch, KnowledgePendingAttachment, KnowledgeSource, KnowledgeUser, KnowledgeUserRole

from conftest import SYSTEM_OWNER_ID


@pytest.fixture
def second_user(session):
    user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(user)
    session.flush()
    return user


# ── resolve_system_owner_id() ───────────────────────────────────────────────

def test_resolve_system_owner_id_returns_seeded_owner(session):
    assert resolve_system_owner_id(session) == SYSTEM_OWNER_ID


def test_resolve_system_owner_id_raises_without_a_system_owner_row(session):
    session.query(KnowledgeUser).delete()
    session.flush()
    with pytest.raises(RuntimeError):
        resolve_system_owner_id(session)


# ── ingest_text(): дедуп per-tenant, не глобальный ─────────────────────────

def test_ingest_text_dedups_within_same_user(session):
    first = ingest_text(session, domain="engineering", text="один и тот же текст")
    session.flush()
    second = ingest_text(session, domain="engineering", text="один и тот же текст")
    session.flush()

    assert first.id == second.id
    assert session.scalar(select(KnowledgeSource.knowledge_user_id).where(
        KnowledgeSource.id == first.id)) == SYSTEM_OWNER_ID


def test_ingest_text_does_not_dedup_across_users(session, second_user):
    owner_source = ingest_text(session, domain="engineering", text="идентичный текст")
    session.flush()
    other_source = ingest_text(session, domain="engineering", text="идентичный текст",
                               knowledge_user_id=second_user.id)
    session.flush()

    assert owner_source.id != other_source.id
    assert owner_source.sha256 == other_source.sha256
    assert owner_source.knowledge_user_id == SYSTEM_OWNER_ID
    assert other_source.knowledge_user_id == second_user.id


# ── register_file_for_ingest(): та же изоляция для файлов ─────────────────

def test_register_file_for_ingest_does_not_dedup_across_users(session, second_user, tmp_path):
    raw_a = tmp_path / "a" / "sample.txt"
    raw_a.parent.mkdir()
    raw_a.write_bytes("одинаковые байты файла".encode("utf-8"))
    raw_b = tmp_path / "b" / "sample.txt"
    raw_b.parent.mkdir()
    raw_b.write_bytes("одинаковые байты файла".encode("utf-8"))

    owner_result = register_file_for_ingest(session, domain="engineering", raw_path=raw_a)
    session.flush()
    other_result = register_file_for_ingest(session, domain="engineering", raw_path=raw_b,
                                            knowledge_user_id=second_user.id)
    session.flush()

    assert owner_result.created is True
    assert other_result.created is True
    assert owner_result.source.id != other_result.source.id
    assert owner_result.source.sha256 == other_result.source.sha256
    assert other_result.job is not None  # НЕ EXACT_DUPLICATE чужого source


# ── chat_intake.stage_attachment(): pending-строка несёт тенант ───────────

def test_stage_attachment_records_resolved_tenant(session, tmp_path):
    pending = stage_attachment(session, channel="telegram", data=b"attachment-bytes",
                               original_filename="note.txt", mime_type="text/plain",
                               spool_root=str(tmp_path / "spool"))
    session.flush()

    assert pending.knowledge_user_id == SYSTEM_OWNER_ID


def test_stage_attachment_honours_explicit_tenant(session, second_user, tmp_path):
    pending = stage_attachment(session, channel="telegram", data=b"attachment-bytes-2",
                               original_filename="note2.txt", mime_type="text/plain",
                               spool_root=str(tmp_path / "spool"),
                               knowledge_user_id=second_user.id)
    session.flush()

    assert pending.knowledge_user_id == second_user.id


# ── batch_intake.stage_batch(): дедуп архива per-tenant ────────────────────

def test_stage_batch_does_not_dedup_archive_across_users(session, second_user, tmp_path, monkeypatch):
    import zipfile

    def make_zip(path):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("a.txt", "содержимое")

    zip_path = tmp_path / "archive.zip"
    make_zip(zip_path)
    data = zip_path.read_bytes()

    owner_result = stage_batch(session, channel="telegram", data=data,
                               original_filename="archive.zip", mime_type=None,
                               raw_batches_root=str(tmp_path / "batches"))
    session.flush()
    other_result = stage_batch(session, channel="telegram", data=data,
                               original_filename="archive.zip", mime_type=None,
                               raw_batches_root=str(tmp_path / "batches"),
                               knowledge_user_id=second_user.id)
    session.flush()

    assert owner_result.batch.id != other_result.batch.id
    assert owner_result.batch.archive_sha256 == other_result.batch.archive_sha256
    assert owner_result.batch.knowledge_user_id == SYSTEM_OWNER_ID
    assert other_result.batch.knowledge_user_id == second_user.id
    assert other_result.waiting_for_domain is True  # НЕ "уже видели" чужой архив


# ── PostgreSQL RLS (v3.8 §14.4 defense in depth) ────────────────────────────
#
# Тесты выше проходят даже без единой RLS-политики — они проверяют
# explicit-предикат в коде (первый слой). Ниже — второй слой отдельно:
# GUC выставляется/убирается напрямую, обходя bind_knowledge_user(), чтобы
# доказать, что БД сама не отдаёт и не принимает чужую строку, даже если
# приложение забудет предикат. См. helm_core/knowledge/rls.py.

def _make_source(session, *, knowledge_user_id: uuid.UUID, sha256_byte: str) -> KnowledgeSource:
    bind_knowledge_user(session, knowledge_user_id)
    source = KnowledgeSource(
        knowledge_user_id=knowledge_user_id, domain="engineering", sha256=sha256_byte * 64,
        raw_path="/tmp/x", sensitivity="internal", trust="extracted", status="active",
    )
    session.add(source)
    session.flush()
    return source


def test_rls_hides_rows_when_no_tenant_is_bound(session, second_user):
    _make_source(session, knowledge_user_id=SYSTEM_OWNER_ID, sha256_byte="a")
    session.flush()

    session.execute(text("RESET app.current_knowledge_user_id"))
    assert session.scalars(select(KnowledgeSource)).all() == []


def test_rls_hides_other_tenants_rows(session, second_user):
    owner_source = _make_source(session, knowledge_user_id=SYSTEM_OWNER_ID, sha256_byte="b")
    other_source = _make_source(session, knowledge_user_id=second_user.id, sha256_byte="c")

    bind_knowledge_user(session, second_user.id)
    visible = session.scalars(select(KnowledgeSource)).all()
    assert [s.id for s in visible] == [other_source.id]

    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    visible = session.scalars(select(KnowledgeSource)).all()
    assert [s.id for s in visible] == [owner_source.id]


def test_rls_rejects_insert_for_a_different_tenant_than_the_bound_guc(session, second_user):
    bind_knowledge_user(session, second_user.id)
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="row-level security"):
        with session.begin_nested():
            session.add(KnowledgeSource(
                knowledge_user_id=SYSTEM_OWNER_ID,  # НЕ совпадает с забинденным GUC
                domain="engineering", sha256="d" * 64, raw_path="/tmp/x",
                sensitivity="internal", trust="extracted", status="active",
            ))
            session.flush()
