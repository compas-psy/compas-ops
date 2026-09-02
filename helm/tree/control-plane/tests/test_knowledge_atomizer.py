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
from helm_core.models import KnowledgeNote, KnowledgeUser, KnowledgeUserRole


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


# ── atomize_and_store(): заморожен на время rescue (R2) ───────────────────
#
# До 02.09.2026 здесь стояли три теста, закреплявшие ровно то поведение,
# которое v4.0 §14.23 называет нарушением: дозапись текста второго
# источника в заметку первого по совпадению slug и `explicit_link` у
# связи, порождённой моделью. Тест, который держит запрещённое свойство,
# хуже отсутствующего — он мешает его убрать. Заменены на проверку
# заморозки; сама заморозка снимается в R3 вместе с новым контрактом.


@pytest.mark.parametrize("domain", ["personal", "ventures", "simpas/company"])
def test_atomize_and_store_writes_nothing_during_rescue(
        session, tmp_path, monkeypatch, user, domain):
    """Ни строки в `knowledge_notes`, ни файла заметки, ни вызова модели.

    Последнее проверяется отдельно: если бы заморозка стояла ПОСЛЕ
    `atomize_or_empty()`, ingest всё равно ходил бы в Ollama на каждом
    источнике — молча и впустую.
    """
    called = []
    monkeypatch.setattr(
        atomizer, "atomize_or_empty",
        lambda text, *, domain: called.append(text) or [
            AtomizedAtom(slug="Иванов", type="PERSON",
                         text="Врач Иванов принял пациента.", links=("Гастроэнтеролог",))],
    )

    count = atomize_and_store(session, domain=domain, knowledge_user_id=user.id,
                              source_id=uuid.uuid4(), source_sha256="sha-1",
                              text="исходный текст про приём", vault_root=str(tmp_path))
    session.flush()

    assert count == 0
    assert called == []
    assert session.query(KnowledgeNote).count() == 0
    assert not (tmp_path / "entities").exists()


def test_same_slug_from_two_sources_no_longer_merges(session, tmp_path, monkeypatch, user):
    """§14.6/§14.23: слияние утверждений по совпадению названия.

    Проверяется не «мёржит правильно», а «не пишет вовсе»: пока писателя
    v2 нет, единственный честный способ не склеивать однофамильцев —
    ничего не записывать.
    """
    monkeypatch.setattr(
        atomizer, "atomize_or_empty",
        lambda text, *, domain: [AtomizedAtom(slug="Иванов", type="PERSON", text=text, links=())],
    )

    for n, sha in ((1, "sha-1"), (2, "sha-2")):
        atomize_and_store(session, domain="personal", knowledge_user_id=user.id,
                          source_id=uuid.uuid4(), source_sha256=sha,
                          text=f"Визит {n}: приём у Иванова.", vault_root=str(tmp_path))
    session.flush()

    assert session.scalars(select(KnowledgeNote).where(KnowledgeNote.slug == "Иванов")).all() == []


def test_ingest_still_works_and_produces_no_l2_notes(session, tmp_path, monkeypatch, user):
    """Сквозная проверка реальной точки входа. Заморожен L2, не ingest:
    источник создаётся, слой 1 (`note_id_for()` по самому источнику)
    продолжает работать, заметки L2 не появляются."""
    monkeypatch.setattr(
        atomizer, "atomize_or_empty",
        lambda text, *, domain: [AtomizedAtom(slug="Проект Симпас", type="ENTITY",
                                              text="Обсуждали дорожную карту.", links=())],
    )

    source = ingest_text(session, domain="ventures", text="Встреча по проекту Симпас.",
                         knowledge_user_id=user.id, vault_root=str(tmp_path))
    session.flush()

    assert source.id is not None
    assert session.query(KnowledgeNote).count() == 0
    # Owner-уровня relations (note_id_for самого источника) продолжают
    # создаваться как раньше — заморожен только L2-слой поверх них.
    assert note_id_for(original_filename=None, source_id=source.id) is not None
