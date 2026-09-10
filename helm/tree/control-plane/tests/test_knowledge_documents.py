"""v3.8 §14.15 — вернуть оригинал документа, а не пересказ.

Главное свойство: отдаются ИСХОДНЫЕ БАЙТЫ с тем же SHA256, а не
разобранный текст под видом оригинала.
"""

import hashlib
import uuid
from pathlib import Path

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


def test_text_source_now_has_a_real_original(session, tmp_path):
    """До 06.09.2026 `ingest_text()` записывал в базу путь к файлу,
    которого не создавал, и §14.15 честно отвечала «исходного файла
    нет». Теперь текст сохраняется целиком, и оригинал у него есть —
    ровно те байты, по которым посчитан sha256."""
    source = ingest_text(session, domain="personal", text="Просто текст",
                         vault_root=str(tmp_path))
    session.flush()

    original = read_original(session, source.id)

    assert original.data == "Просто текст".encode("utf-8")
    assert original.sha256 == source.sha256


def test_source_without_a_file_on_disk_is_refused(session):
    """Путь есть, файла нет — отдавать под видом оригинала нечего."""
    source = ingest_text(session, domain="personal", text="Пропавший текст")
    session.flush()
    Path(source.raw_path).unlink()

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


def test_подбор_кандидатов_не_приносит_постороннее(session):
    """«Последний клинический анализ крови» — это анализы, а не книга.

    Скриншот владельца 10.09.2026: под этот запрос бот предложил книгу по
    психологическому консультированию, два queue-recovery и MASTER_TZ.md.
    Причина в `find_sources`: поиск по имени требует, чтобы совпали ВСЕ
    слова запроса, слова «последнего» нет ни в одном имени — и запрос
    проваливается в полнотекстовый поиск по отдельным словам, где книга
    выигрывает объёмом.
    """
    from helm_core.knowledge.documents import find_sources

    bind_knowledge_user(session, None)
    ingest_text(session, domain="health", text="Гемоглобин 167 г/л",
                original_filename="94574021_Клинический анализ крови.pdf")
    ingest_text(session, domain="health", text="HbA1c 5.4 %",
                original_filename="148990953_Исследования гликированного гемоглобина.pdf")
    ingest_text(session, domain="personal",
                text="Клинический анализ случая. Последний раз консультирование "
                     "рассматривало анализ переноса и крови как метафоры.",
                original_filename="Психологическое_консультирование_Теория_и практика.fb2")
    session.flush()

    found = find_sources(session, query="последнего клинического анализа крови")
    names = [candidate.original_filename for candidate in found]

    assert names, "хоть один кандидат обязан найтись"
    assert all("Клинический анализ крови" in (name or "") for name in names), names
