"""v4.0 §14.5 — схема semantic-v2.

Здесь проверяется только схема: R2 по rescue-plan это таблицы, изоляция
и health-адаптеры, писателя графа ещё нет (он в R3). Поэтому тесты
отвечают на три вопроса.

1. Держит ли RLS новые таблицы. Узел с каноническим именем «Безручко
   Дарья Юрьевна» — такой же чужой контент, как чанк; таблица, забытая в
   `TENANT_SCOPED_TABLES`, отдала бы его соседу.
2. Разрешает ли схема то, ради чего v2 и заводится: два разных
   утверждения с ОДИНАКОВЫМ именем. Именно это semantic-v1 делать не
   давал — там `UNIQUE (knowledge_user_id, slug)` вынуждал дописывать
   текст второго источника в заметку первого (§14.6, «same slug → append
   text» — forbidden).
3. Уехало ли в health то, что называет тему. `canonical_label` и `alias`
   — это «визит к гастроэнтерологу» и «Безручко Д.Ю.»: ровно те health
   entities/topics, которым в public быть нельзя.

Чего здесь нет намеренно: проверки «текущей может стать только ревизия
READY» (§14.5). Правило есть, а места, где его нарушить, ещё нет —
переключением занимается R3. Заводить сейчас сторожа без единого
писателя значит писать код впрок (CLAUDE.md §2); правило записано в
докстринге модели и в V4.0-RESCUE-DELTA.md как обязательство R3.
"""

import uuid

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.rls import TENANT_SCOPED_TABLES
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (
    KnowledgeEdge, KnowledgeEntityAlias, KnowledgeNode, KnowledgeNodeMention,
    KnowledgeSemanticRun, KnowledgeUser, KnowledgeUserRole,
    SemanticEvidenceType, SemanticNodeKind, SemanticRelationType, SemanticRunStatus,
)
from helm_core.models.health_tables import HealthBase

from conftest import SYSTEM_OWNER_ID

SEMANTIC_V2_TABLES = (
    "knowledge_semantic_runs", "knowledge_nodes", "knowledge_node_mentions",
    "knowledge_edges", "knowledge_entity_aliases",
)


@pytest.fixture
def second_user(session):
    user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(user)
    session.flush()
    return user


def _make_graph(session, *, user_id, label, source):
    """Минимальный связный кусок графа: прогон → сущность → упоминание →
    ребро → алиас. Меньше нельзя: ребро без узлов и упоминание без
    источника схемой не принимаются, а проверять надо все пять таблиц."""
    run = KnowledgeSemanticRun(
        knowledge_user_id=user_id, source_id=source.id, semantic_version=2,
        status=SemanticRunStatus.READY, windows_total=1, windows_processed=1,
        windows_failed=0, nodes_created=1, edges_created=1, unresolved_candidates=0,
    )
    session.add(run)
    session.flush()

    entity = KnowledgeNode(
        knowledge_user_id=user_id, kind=SemanticNodeKind.ENTITY, entity_type="person",
        subtype="PERSON", canonical_label=label, normalized_key=label.lower(),
        semantic_run_id=run.id,
    )
    event = KnowledgeNode(
        knowledge_user_id=user_id, kind=SemanticNodeKind.EVENT,
        canonical_label=f"визит к {label}", statement_text=f"Состоялся визит к {label}.",
        semantic_run_id=run.id,
    )
    session.add_all([entity, event])
    session.flush()

    mention = KnowledgeNodeMention(
        knowledge_user_id=user_id, node_id=entity.id, source_id=source.id,
        window_id=0, evidence_type=SemanticEvidenceType.EXTRACTED, semantic_run_id=run.id,
    )
    session.add(mention)
    session.flush()

    session.add_all([
        KnowledgeEdge(
            knowledge_user_id=user_id, from_node_id=event.id, to_node_id=entity.id,
            relation_type=SemanticRelationType.INVOLVES, role="doctor",
            source_id=source.id, mention_id=mention.id,
            evidence_type=SemanticEvidenceType.EXTRACTED, semantic_run_id=run.id,
        ),
        KnowledgeEntityAlias(
            knowledge_user_id=user_id, entity_node_id=entity.id,
            alias=label, normalized_alias=label.lower(), source_id=source.id,
        ),
    ])
    session.flush()
    return run, entity, event


# ── 1. изоляция ────────────────────────────────────────────────────────────

def test_semantic_v2_tables_are_in_the_rls_list() -> None:
    """Список читается и миграцией, и тестовой фикстурой. Таблица, не
    попавшая в него, создаётся вообще без политики — и молча."""
    missing = [t for t in SEMANTIC_V2_TABLES if t not in TENANT_SCOPED_TABLES]
    assert not missing, f"без RLS остались: {missing}"


@pytest.mark.parametrize("table", SEMANTIC_V2_TABLES)
def test_rls_is_forced_on_semantic_v2_tables(session, table) -> None:
    """`ENABLE` без `FORCE` не действует на владельца таблицы — а владелец
    здесь та же роль, что обслуживает рантайм (см. rls.py)."""
    row = session.execute(text(
        "select relrowsecurity, relforcerowsecurity from pg_class "
        "where relname = :t and relnamespace = 'public'::regnamespace"
    ), {"t": table}).one()
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True


def test_two_tenants_see_only_their_own_semantic_graph(engine, session, second_user):
    """Поведенческая проверка, а не «RLS/FORCE = t».

    Включённая политика и работающая изоляция — разные утверждения:
    политика с неверным предикатом тоже показывает `t`. Поэтому здесь
    два реальных владельца, строки во ВСЕХ пяти таблицах у каждого, и
    три счётчика: A→B, B→A и «тенант не выставлен».
    """
    first_id, second_id = SYSTEM_OWNER_ID, second_user.id
    session.commit()

    made = {}
    for user_id, label in ((first_id, "Первый врач"), (second_id, "Второй врач")):
        with Session(engine) as s:
            bind_knowledge_user(s, user_id)
            source = ingest_text(s, domain="health", text=f"приём, {label}",
                                 knowledge_user_id=user_id)
            s.flush()
            _make_graph(s, user_id=user_id, label=label, source=source)
            s.commit()
        made[user_id] = label

    for mine, theirs in ((first_id, second_id), (second_id, first_id)):
        with Session(engine) as s:
            bind_knowledge_user(s, mine)
            for model in (KnowledgeSemanticRun, KnowledgeNode, KnowledgeNodeMention,
                          KnowledgeEdge, KnowledgeEntityAlias):
                rows = s.scalars(select(model)).all()
                assert rows, f"{model.__name__}: свои строки не видны — тест ничего не проверяет"
                foreign = [r for r in rows if r.knowledge_user_id != mine]
                assert not foreign, (
                    f"{model.__name__}: видно {len(foreign)} строк владельца {theirs}")
            labels = {n.canonical_label for n in s.scalars(select(KnowledgeNode)).all()}
            assert made[theirs] not in " ".join(labels)

    # Тенант не выставлен — ноль строк везде, а не «всё подряд».
    # Пустая строка проверяется отдельно от «GUC не трогали»: на пуле
    # соединений Postgres оставляет placeholder со значением '', и
    # именно этот случай когда-то ронял запрос вместо того, чтобы
    # вернуть ноль строк (см. PREDICATE в rls.py).
    with Session(engine) as s:
        s.execute(text("SET LOCAL app.current_knowledge_user_id = ''"))
        for model in (KnowledgeSemanticRun, KnowledgeNode, KnowledgeNodeMention,
                      KnowledgeEdge, KnowledgeEntityAlias):
            assert s.scalars(select(model)).all() == [], model.__name__


def test_writing_a_node_for_another_user_is_refused(session, second_user):
    """`WITH CHECK` той же политики: подставить чужой id в свою строку
    нельзя. Без этого изоляция была бы только на чтение."""
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    session.add(KnowledgeNode(
        knowledge_user_id=second_user.id, kind=SemanticNodeKind.ENTITY,
        entity_type="person", canonical_label="чужая сущность",
    ))
    with pytest.raises(Exception) as err:
        session.flush()
    assert "row-level security" in str(err.value).lower()


# ── 2. запрет слияния по имени ─────────────────────────────────────────────

def test_two_atoms_with_the_same_label_coexist(session):
    """§14.6: два визита к одному врачу из двух источников — ДВА узла.

    В semantic-v1 на этом месте стоял `UNIQUE (knowledge_user_id, slug)`,
    и второй источник дописывался в заметку первого. Тест держит именно
    отсутствие такого ограничения, а не его наличие.
    """
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    first_source = ingest_text(session, domain="health", text="первый приём")
    second_source = ingest_text(session, domain="health", text="второй приём")
    session.flush()

    _, _, first_event = _make_graph(
        session, user_id=SYSTEM_OWNER_ID, label="Безручко", source=first_source)
    _, _, second_event = _make_graph(
        session, user_id=SYSTEM_OWNER_ID, label="Безручко", source=second_source)

    assert first_event.id != second_event.id
    assert first_event.canonical_label == second_event.canonical_label
    mentions = session.scalars(select(KnowledgeNodeMention)).all()
    assert {m.source_id for m in mentions} == {first_source.id, second_source.id}


def test_no_unique_constraint_merges_nodes_by_label(session) -> None:
    """Страховка от возврата слияния по имени другим путём — уникальным
    индексом, добавленным «чтобы не плодить дубли»."""
    indexes = inspect(session.get_bind()).get_indexes("knowledge_nodes")
    unique_over_label = [
        ix for ix in indexes
        if ix["unique"] and {"canonical_label", "normalized_key"} & set(ix["column_names"])
    ]
    assert not unique_over_label, (
        f"уникальность по имени узла вернулась: {unique_over_label} — §14.6 "
        "запрещает сливать утверждения по совпадению названия"
    )


def test_alias_uniqueness_is_scoped_to_one_entity(session, second_user):
    """Алиас уникален в пределах пары (владелец, сущность), не глобально:
    «Безручко Д.Ю.» у двух разных людей — законная ситуация (однофамильцы
    как раз тот случай, ради которого §14.6 запрещает слияние по имени)."""
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    source = ingest_text(session, domain="health", text="приём")
    session.flush()
    _, first_entity, _ = _make_graph(
        session, user_id=SYSTEM_OWNER_ID, label="Безручко Д.Ю.", source=source)

    second_entity = KnowledgeNode(
        knowledge_user_id=SYSTEM_OWNER_ID, kind=SemanticNodeKind.ENTITY,
        entity_type="person", subtype="PERSON",
        canonical_label="Безручко Д.Ю. (другой)", normalized_key="безручко д.ю. (другой)",
    )
    session.add(second_entity)
    session.flush()
    session.add(KnowledgeEntityAlias(
        knowledge_user_id=SYSTEM_OWNER_ID, entity_node_id=second_entity.id,
        alias="Безручко Д.Ю.", normalized_alias="безручко д.ю.",
    ))
    session.flush()

    assert first_entity.id != second_entity.id
    assert len(session.scalars(select(KnowledgeEntityAlias)).all()) == 2


# ── 3. health-адаптеры ─────────────────────────────────────────────────────

def test_health_mirrors_exist_for_content_bearing_tables() -> None:
    """Четыре из пяти. `knowledge_semantic_runs` в health не зеркалится —
    в ней нет ни одного поля с содержимым источника."""
    mirrored = {t.name for t in HealthBase.metadata.tables.values()}
    assert {"knowledge_nodes", "knowledge_node_mentions",
            "knowledge_edges", "knowledge_entity_aliases"} <= mirrored
    assert "knowledge_semantic_runs" not in mirrored


def test_health_mirrors_carry_the_fields_that_name_a_topic() -> None:
    """Смысл зеркала — увести из public то, что называет тему. Если
    `canonical_label`/`alias`/`role` в зеркале нет, зеркало не нужно."""
    tables = HealthBase.metadata.tables
    assert "canonical_label" in tables["health.knowledge_nodes"].columns
    assert "normalized_key" in tables["health.knowledge_nodes"].columns
    assert "alias" in tables["health.knowledge_entity_aliases"].columns
    assert "role" in tables["health.knowledge_edges"].columns


def test_health_mirrors_do_not_reference_public() -> None:
    """`helm_health` не имеет на public вообще никаких прав, поэтому ни
    один внешний ключ зеркала не должен туда указывать — включая
    `semantic_run_id`, у которого FK нет вовсе (целостность в коде, как у
    `source_id` сайдкара)."""
    for table in HealthBase.metadata.tables.values():
        for fk in table.foreign_keys:
            assert fk.column.table.schema == "health", (
                f"{table.name}.{fk.parent.name} ссылается за пределы health: "
                f"{fk.target_fullname}"
            )
