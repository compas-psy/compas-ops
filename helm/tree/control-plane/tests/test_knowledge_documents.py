"""v3.8 §14.15 — вернуть оригинал документа, а не пересказ.

Главное свойство: отдаются ИСХОДНЫЕ БАЙТЫ с тем же SHA256, а не
разобранный текст под видом оригинала.
"""

import hashlib
import uuid

import pytest

from helm_core.knowledge.documents import (
    DocumentUnavailable, find_sources, is_sensitive, read_original,
)
from helm_core.knowledge.ingest import ingest_text, register_file_for_ingest
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeStatus, KnowledgeUser, KnowledgeUserRole

PDF_BYTES = b"%PDF-1.4 fake contract bytes"


@pytest.fixture
def secondary(session):
    user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(user)
    session.flush()
    return user


def _upload(session, tmp_path, *, name="contract.pdf", data=PDF_BYTES, user_id=None):
    """Файл уже лежит на диске — ровно так его видит
    `register_file_for_ingest()` после переноса из спула."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(exist_ok=True)
    raw_path = raw_dir / f"{hashlib.sha256(data).hexdigest()}-{name}"
    raw_path.write_bytes(data)
    result = register_file_for_ingest(
        session, domain="engineering", raw_path=raw_path, original_filename=name,
        mime_type="application/pdf", vault_root=str(tmp_path / "vault"),
        knowledge_user_id=user_id)
    session.flush()
    return result.source


# ── поиск ────────────────────────────────────────────────────────────────

def test_find_by_filename(session, tmp_path):
    _upload(session, tmp_path, name="contract-2026.pdf")

    found = find_sources(session, query="contract")

    assert len(found) == 1
    assert found[0].original_filename == "contract-2026.pdf"
    assert found[0].sha256 == hashlib.sha256(PDF_BYTES).hexdigest()


def test_find_falls_back_to_content_when_the_name_says_nothing(session, tmp_path):
    """Люди просят файл по имени, но не всегда его помнят."""
    ingest_text(session, domain="engineering", text="Договор с подрядчиком на кровлю.",
                original_filename="scan-0031.md")
    session.flush()

    by_content = find_sources(session, query="подрядчиком кровлю")

    assert [c.original_filename for c in by_content] == ["scan-0031.md"]


def test_archived_document_is_still_findable_for_review(session, tmp_path):
    """§14.15: заархивированное можно скачать владельцу для разбора, хотя
    в обычных ответах оно не участвует."""
    source = _upload(session, tmp_path)
    source.status = KnowledgeStatus.ARCHIVED
    session.flush()

    assert find_sources(session, query="contract")


def test_search_never_returns_another_users_document(session, secondary, tmp_path):
    owner_id = bind_knowledge_user(session, None)
    _upload(session, tmp_path, name="owner-secret.pdf", user_id=owner_id)

    found = find_sources(session, query="owner-secret", knowledge_user_id=secondary.id)

    assert found == []


# ── выдача байт ──────────────────────────────────────────────────────────

def test_download_returns_the_exact_original_bytes(session, tmp_path):
    source = _upload(session, tmp_path)

    original = read_original(session, source.id)

    assert original.data == PDF_BYTES
    assert original.sha256 == hashlib.sha256(PDF_BYTES).hexdigest()
    assert original.filename == "contract.pdf"
    assert original.media_type == "application/pdf"
    assert original.review_only is False


def test_archived_download_is_marked_review_only(session, tmp_path):
    source = _upload(session, tmp_path)
    source.status = KnowledgeStatus.ARCHIVED
    session.flush()

    original = read_original(session, source.id)

    assert original.data == PDF_BYTES
    assert original.review_only is True


def test_another_users_document_is_not_found_not_forbidden(session, secondary, tmp_path):
    """Сообщение то же, что для несуществующего: существование чужого
    файла — тоже сведения о нём."""
    owner_id = bind_knowledge_user(session, None)
    source = _upload(session, tmp_path, user_id=owner_id)

    with pytest.raises(DocumentUnavailable, match="не найден"):
        read_original(session, source.id, knowledge_user_id=secondary.id)


def test_unknown_document_is_refused(session):
    with pytest.raises(DocumentUnavailable, match="не найден"):
        read_original(session, uuid.uuid4())


def test_text_only_source_says_it_has_no_original(session):
    """`ingest_text()` файла на диск не пишет вовсе. Честнее сказать это,
    чем отдать разобранный текст под видом оригинала."""
    source = ingest_text(session, domain="personal", text="Просто текст")
    session.flush()

    with pytest.raises(DocumentUnavailable, match="нет исходного файла"):
        read_original(session, source.id)


def test_tampered_file_is_refused_not_returned(session, tmp_path):
    """Расхождение с записанной контрольной суммой — потеря доказуемости
    происхождения (§14.1 RAW immutable), а не «немного не то»."""
    source = _upload(session, tmp_path)
    from pathlib import Path
    Path(source.raw_path).write_bytes("подменённое содержимое".encode("utf-8"))

    with pytest.raises(DocumentUnavailable, match="контрольной суммой"):
        read_original(session, source.id)


def test_client_content_is_marked_sensitive(session, tmp_path):
    source = _upload(session, tmp_path)
    source.sensitivity = "client_restricted"
    session.flush()

    assert is_sensitive(session, source.id) is True
