"""ADR-019 semantic atomizer (`helm_core/knowledge/atomizer.py`).

Домены здесь намеренно НЕ health по умолчанию (`personal`/`ventures`) —
атомизатор домено-агностичен, и его собственный тест-набор должен это
доказывать первым делом, не через один health-пример с "и то же для
остальных" в комментарии. Health-специфичная маршрутизация (public vs
`health.knowledge_notes`) проверяется отдельно, рядом с такими же
проверками для chunks/relations, в `test_knowledge_health_isolation.py`
— это тот файл, чья единственная задача — тестировать именно
маршрутизацию, а не сам атомизатор.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from helm_core.knowledge import atomizer
from helm_core.knowledge.atomizer import (
    AtomizedAtom, AtomizerUnavailable, _slugify, atomize_and_store, atomize_or_empty,
)
from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.relations import note_id_for
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeNote, KnowledgeRelation, KnowledgeUser, KnowledgeUserRole


@pytest.fixture
def user(session):
    u = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(u)
    session.flush()
    # knowledge_notes несёт ту же RLS-политику (ADR-030), что и остальные
    # tenant-scoped таблицы — прямой вызов atomize_and_store() в обход
    # ingest_text()/register_file_for_ingest() (которые делают это сами)
    # должен привязать тенанта явно, иначе WITH CHECK у INSERT падает.
    bind_knowledge_user(session, u.id)
    return u


# ── _slugify ────────────────────────────────────────────────────────────

def test_slugify_strips_forbidden_filesystem_characters():
    assert _slugify('Иванов/Петров: "уролог"?') == "ИвановПетров уролог"


def test_slugify_collapses_whitespace_and_truncates():
    assert _slugify("  a   " + "б" * 200) == ("a " + "б" * 200)[:128]


# ── atomize()/atomize_or_empty() — разбор ответа модели ───────────────────

def test_atomize_or_empty_returns_empty_list_when_ollama_unavailable(monkeypatch):
    def _raise(text, *, domain):
        raise AtomizerUnavailable("connection refused")
    monkeypatch.setattr(atomizer, "atomize", _raise)

    assert atomize_or_empty("любой текст", domain="personal") == []


def test_atomize_drops_atoms_missing_required_fields():
    """Та же дисциплина, что `extract_frontmatter_relations()`: запись без
    обязательного поля пропускается целиком, не додумывается."""
    raw = (
        '[{"slug": "Годный", "type": "CONCEPT", "text": "текст"}, '
        '{"slug": "", "type": "CONCEPT", "text": "без slug"}, '
        '{"slug": "Без типа", "type": "НЕИЗВЕСТНО", "text": "текст"}, '
        '{"slug": "Без текста", "type": "FACT", "text": ""}]'
    )
    atoms = atomizer._parse_atoms(raw)

    assert [a.slug for a in atoms] == ["Годный"]


def test_parse_atoms_accepts_single_object_instead_of_array():
    """Живьём (02.09.2026) gemma2:2b вернула ОДИН объект вместо массива —
    сама сущность при этом корректная, терять её из-за формы обёртки
    нельзя."""
    raw = '{"slug": "Клинико-диагностический центр", "type": "ORGANIZATION", "text": "на Красной Пресне"}'

    atoms = atomizer._parse_atoms(raw)

    assert [a.slug for a in atoms] == ["Клинико-диагностический центр"]


def test_parse_atoms_accepts_array_wrapped_in_object():
    raw = '{"результат": [{"slug": "Петров", "type": "PERSON", "text": "кардиолог"}]}'

    atoms = atomizer._parse_atoms(raw)

    assert [a.slug for a in atoms] == ["Петров"]


def test_atomize_caps_atoms_per_call():
    raw = "[" + ",".join(
        f'{{"slug": "атом{i}", "type": "CONCEPT", "text": "т"}}'
        for i in range(atomizer.MAX_ATOMS_PER_CALL + 10)
    ) + "]"

    atoms = atomizer._parse_atoms(raw)

    assert len(atoms) == atomizer.MAX_ATOMS_PER_CALL


# ── atomize_and_store()/store_notes() — доменно-агностичный сквозной путь ─

@pytest.mark.parametrize("domain", ["personal", "ventures", "simpas/company"])
def test_atomize_and_store_creates_note_file_and_relation(session, tmp_path, monkeypatch, user, domain):
    monkeypatch.setattr(
        atomizer, "atomize_or_empty",
        lambda text, *, domain: [AtomizedAtom(slug="Иванов", type="PERSON",
                                              text="Врач Иванов принял пациента.",
                                              links=("Гастроэнтеролог",))],
    )

    source = ingest_text(session, domain=domain, text="исходный текст про приём",
                         knowledge_user_id=user.id, vault_root=str(tmp_path))
    session.flush()

    note = session.scalar(select(KnowledgeNote).where(KnowledgeNote.slug == "Иванов"))
    assert note.type == "PERSON"
    assert note.domain == domain
    assert note.source_ids == [str(source.id)]
    assert note.source_sha256 == [source.sha256]

    file_text = (tmp_path / "entities" / "Иванов.md").read_text(encoding="utf-8")
    assert "id: Иванов" in file_text
    assert "Врач Иванов принял пациента." in file_text
    assert "[[Гастроэнтеролог]]" in file_text

    relation = session.scalar(select(KnowledgeRelation).where(KnowledgeRelation.from_id == "Иванов"))
    assert relation.to_id == "Гастроэнтеролог"
    assert relation.evidence_type == "explicit_link"


def test_atomize_and_store_is_fail_open_when_atomizer_unavailable(session, tmp_path, monkeypatch, user):
    monkeypatch.setattr(atomizer, "atomize_or_empty", lambda text, *, domain: [])

    count = atomize_and_store(session, domain="personal", knowledge_user_id=user.id,
                              source_id=uuid.uuid4(), source_sha256="abc123",
                              text="исходный текст", vault_root=str(tmp_path))

    assert count == 0
    assert session.query(KnowledgeNote).count() == 0


def test_atomize_and_store_merges_same_slug_across_two_sources_instead_of_duplicating(
        session, tmp_path, monkeypatch, user):
    """Тот же врач упомянут в двух РАЗНЫХ документах — одна заметка с
    двумя source_ids, не UniqueConstraint(knowledge_user_id, slug)."""
    monkeypatch.setattr(
        atomizer, "atomize_or_empty",
        lambda text, *, domain: [AtomizedAtom(slug="Иванов", type="PERSON", text=text, links=())],
    )
    source_1, source_2 = uuid.uuid4(), uuid.uuid4()

    atomize_and_store(session, domain="personal", knowledge_user_id=user.id, source_id=source_1,
                      source_sha256="sha-1", text="Визит 1: приём у Иванова.",
                      vault_root=str(tmp_path))
    session.flush()
    atomize_and_store(session, domain="personal", knowledge_user_id=user.id, source_id=source_2,
                      source_sha256="sha-2", text="Визит 2: повторный приём у Иванова.",
                      vault_root=str(tmp_path))
    session.flush()

    notes = session.scalars(select(KnowledgeNote).where(KnowledgeNote.slug == "Иванов")).all()
    assert len(notes) == 1
    assert notes[0].source_ids == [str(source_1), str(source_2)]

    file_text = (tmp_path / "entities" / "Иванов.md").read_text(encoding="utf-8")
    assert "Визит 1" in file_text
    assert "Визит 2" in file_text  # дописано, не перезаписано поверх


def test_atomize_and_store_wired_into_ingest_text(session, tmp_path, monkeypatch, user):
    """Сквозная проверка реальной точки входа — ingest_text() вызывает
    атомизатор аддитивно, поверх уже существующего store_relations()."""
    monkeypatch.setattr(
        atomizer, "atomize_or_empty",
        lambda text, *, domain: [AtomizedAtom(slug="Проект Симпас", type="ENTITY",
                                              text="Обсуждали дорожную карту.", links=())],
    )

    source = ingest_text(session, domain="ventures", text="Встреча по проекту Симпас.",
                         knowledge_user_id=user.id, vault_root=str(tmp_path))
    session.flush()

    note = session.scalar(select(KnowledgeNote).where(KnowledgeNote.slug == "Проект Симпас"))
    assert note is not None
    assert str(source.id) in note.source_ids
    # Owner-уровня relations (note_id_for самого источника) продолжают
    # создаваться как раньше — атомизатор не заменяет слой 1, дополняет.
    assert note_id_for(original_filename=None, source_id=source.id) is not None
