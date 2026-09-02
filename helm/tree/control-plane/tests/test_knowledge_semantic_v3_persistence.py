"""R3.1 — round-trip regression на потерю данных, найденную владельцем
02.09.2026 при приёмке R3.

Прогон #177 был зелёным с BLOCKER'ом внутри: `_write_extraction()`
извлекал `atom.text` (законченное утверждение модели, §14.4.2), но нигде
его не сохранял — в узел уходил только заголовок (`atom.title`).
Одновременно `subtype` сущности подменялся её `entity_type`, и настоящий
подвид («doctor», «medical_specialty») терялся молча. Ни один
существовавший тест этого не поймал: extractor-тесты проверяли только
разбор ответа модели, а publish-тесты — только механику публикации
(окна, ревизии, гейт), не содержимое итоговых полей узла.

Поэтому здесь — не unit-тест на `_write_extraction()` и не тест на
extractor, а сквозная проверка через настоящий `publish_semantic_run()`
с перечитыванием из СВЕЖЕЙ сессии после commit. Свежая сессия — не
формальность: ORM-объект, к которому ещё не притронулся `expire`,
показал бы значение, которое разработчик положил в конструктор, а не
то, что реально уехало в базу и приехало обратно.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.semantic_extract import (
    ExtractedAtom, ExtractedEdge, ExtractedEntity, WindowExtraction,
)
from helm_core.knowledge.semantic_publish import publish_semantic_run
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeNode, SemanticRunStatus

from conftest import SYSTEM_OWNER_ID


def _one_window_extractor(extraction: WindowExtraction):
    """Извлекатель, отдающий один и тот же разбор для любого окна.

    Тестовый документ короткий и укладывается в одно окно — так число
    вызовов предсказуемо, и в графе оказывается ровно то, что описано
    в тесте, ни одним узлом больше."""
    def extract(window_text, *, domain, heading_path=(), model=""):
        return extraction
    return extract


@pytest.fixture
def source(session):
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    src = ingest_text(session, domain="personal", text="Короткий документ для проверки записи.")
    session.flush()
    return src


def _refetch_nodes(engine, run_id) -> list[KnowledgeNode]:
    """Перечитать узлы ревизии из НОВОЙ сессии — доказательство того, что
    значения пережили commit, а не остались только в памяти Python."""
    with Session(engine) as fresh:
        bind_knowledge_user(fresh, SYSTEM_OWNER_ID)
        return list(fresh.scalars(
            select(KnowledgeNode).where(KnowledgeNode.semantic_run_id == run_id)
            .order_by(KnowledgeNode.created_at)))


# ── PERSON/doctor: обе классификации сущности ────────────────────────────

def test_entity_type_and_subtype_both_survive_commit(engine, session, source):
    extraction = WindowExtraction(entities=[
        ExtractedEntity(local_id="e1", entity_type="PERSON", subtype="doctor",
                        label="Кириченко Сергей Александрович"),
    ])
    result = publish_semantic_run(session, source=source, text="текст",
                                  extract=_one_window_extractor(extraction))
    session.commit()
    assert result.status == SemanticRunStatus.READY

    nodes = _refetch_nodes(engine, result.run_id)
    assert len(nodes) == 1
    node = nodes[0]
    assert node.kind == "entity"
    assert node.entity_type == "person"
    assert node.subtype == "doctor"
    assert node.canonical_label == "Кириченко Сергей Александрович"
    assert node.statement_text is None


# ── CONCEPT/medical_specialty: обе классификации, вторая пара значений ───

def test_concept_entity_type_and_subtype_both_survive_commit(engine, session, source):
    extraction = WindowExtraction(entities=[
        ExtractedEntity(local_id="e1", entity_type="CONCEPT", subtype="medical_specialty",
                        label="гастроэнтеролог"),
    ])
    result = publish_semantic_run(session, source=source, text="текст",
                                  extract=_one_window_extractor(extraction))
    session.commit()

    nodes = _refetch_nodes(engine, result.run_id)
    assert len(nodes) == 1
    node = nodes[0]
    assert node.entity_type == "concept"
    assert node.subtype == "medical_specialty"
    assert node.canonical_label == "гастроэнтеролог"


# ── FACT: title и text — разные значения, оба должны сохраниться ────────

def test_fact_title_and_text_are_both_preserved_and_distinct(engine, session, source):
    title = "Диагноз подтверждён"
    text = ("Диагноз подтверждён по результатам анализа крови от 19 августа: "
           "выявлен дефицит железа, назначена терапия препаратами железа.")
    assert title != text, "заголовок и текст фикстуры обязаны отличаться — иначе не тест, а совпадение"

    extraction = WindowExtraction(atoms=[
        ExtractedAtom(local_id="a1", kind="fact", title=title, text=text),
    ])
    result = publish_semantic_run(session, source=source, text="текст",
                                  extract=_one_window_extractor(extraction))
    session.commit()

    nodes = _refetch_nodes(engine, result.run_id)
    assert len(nodes) == 1
    node = nodes[0]
    assert node.canonical_label == title
    assert node.statement_text == text
    assert node.canonical_label != node.statement_text
    assert node.entity_type is None


# ── DECISION: многострочный statement_text не обрезан и не заменён ──────

def test_decision_multiline_statement_text_is_not_truncated_or_replaced(engine, session, source):
    title = "Решение по подписке"
    text = (
        "Обсудили тарификацию на встрече 19 августа.\n"
        "Решили не делать платную подписку в релизе 1.\n"
        "Причина: нет ступени синтеза ответа, только цитирование источника.\n"
        "Пересмотреть вопрос после релиза 2, когда появится Z2-рефраз."
    )
    extraction = WindowExtraction(atoms=[
        ExtractedAtom(local_id="a1", kind="decision", title=title, text=text),
    ])
    result = publish_semantic_run(session, source=source, text="текст",
                                  extract=_one_window_extractor(extraction))
    session.commit()

    nodes = _refetch_nodes(engine, result.run_id)
    assert len(nodes) == 1
    node = nodes[0]
    assert node.statement_text == text, "многострочный текст обрезан или изменён при записи/чтении"
    assert node.statement_text.count("\n") == 3
    assert node.canonical_label == title
    assert title not in node.statement_text.split("\n")[1:], (
        "тело подменено заголовком на строках после первой")


# ── Обе потери разом, на одном прогоне: сущность + утверждение + ребро ──

def test_entity_and_atom_do_not_cross_contaminate_their_fields(engine, session, source):
    """Регрессия именно на класс дефекта: до фикса запись сущности и
    запись атома делили одну и ту же ошибку — оба писали классификатор
    не в то поле. Здесь оба вида в одном прогоне, и поля не должны
    перепутаться местами."""
    extraction = WindowExtraction(
        entities=[ExtractedEntity(local_id="e1", entity_type="PERSON", subtype="doctor",
                                  label="Иванов")],
        atoms=[ExtractedAtom(local_id="a1", kind="event", title="Приём",
                             text="Приём у Иванова состоялся.")],
        edges=[ExtractedEdge(from_local_id="a1", relation_type="involves",
                             to_local_id="e1", role="doctor")],
    )
    result = publish_semantic_run(session, source=source, text="текст",
                                  extract=_one_window_extractor(extraction))
    session.commit()

    nodes = {n.kind: n for n in _refetch_nodes(engine, result.run_id)}
    assert nodes["entity"].entity_type == "person"
    assert nodes["entity"].subtype == "doctor"
    assert nodes["entity"].statement_text is None
    assert nodes["event"].entity_type is None
    assert nodes["event"].statement_text == "Приём у Иванова состоялся."
    assert nodes["event"].canonical_label == "Приём"
