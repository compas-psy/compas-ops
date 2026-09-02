"""v4.0 §14.5/§14.9 — закрытые реестры semantic-v2 закрыты и в базе.

§14.9 называет реестр связей закрытым: «модель не изобретает типы
связей». Enum в Python этого не обеспечивает — он ограничивает ровно
один путь записи. Мимо него ходят миграции, backfill, `psql` руками и
любой код, собирающий INSERT строкой. Реестр, закрытый только в Python,
закрыт наполовину.

Тесты идут прямым SQL мимо ORM — иначе проверялся бы enum, а не СУБД.

Дополнительно: обе схемы (public и health) обязаны нести ОДИН реестр.
Health-путь пишет отдельная роль по отдельному соединению, и «в public
проверим» его не касается; разъехавшиеся реестры означали бы, что в
health можно записать то, чего нельзя в общей схеме.
"""

import uuid

import pytest
import sqlalchemy.exc
from sqlalchemy import text

from helm_core.models import Base
from helm_core.models.base import (
    NODE_KINDS_WITHOUT_RUN, SemanticDatePrecision, SemanticEvidenceType, SemanticNodeKind,
    SemanticNodeStatus, SemanticRelationType, SemanticRunStatus,
)
from helm_core.models.health_tables import HealthBase

#: (таблица, колонка, перечисление). Ровно тот список, который назвал
#: владелец 02.09.2026; порядок сохранён.
REGISTRIES = [
    ("knowledge_nodes", "kind", SemanticNodeKind),
    ("knowledge_nodes", "status", SemanticNodeStatus),
    ("knowledge_nodes", "date_precision", SemanticDatePrecision),
    ("knowledge_node_mentions", "evidence_type", SemanticEvidenceType),
    ("knowledge_edges", "relation_type", SemanticRelationType),
    ("knowledge_edges", "evidence_type", SemanticEvidenceType),
    ("knowledge_edges", "status", SemanticNodeStatus),
    ("knowledge_semantic_runs", "status", SemanticRunStatus),
]


def _check_sql(metadata, table: str, column: str) -> str:
    """Текст CHECK, стоящего в базе на этой колонке, — из метаданных
    модели. Сверять надо с тем, что реально поедет в базу, а не с
    отдельно записанным списком: второй список разъедется первым."""
    key = table if table in metadata.tables else f"health.{table}"
    for constraint in metadata.tables[key].constraints:
        name = str(constraint.name or "")
        if name.endswith(f"_{column}") and name.startswith("ck_"):
            return str(constraint.sqltext)
    raise AssertionError(f"на {key}.{column} нет CHECK — реестр закрыт только в Python")


@pytest.mark.parametrize("table,column,enum_cls", REGISTRIES,
                         ids=lambda v: getattr(v, "__name__", str(v)))
def test_public_registry_matches_the_enum(table, column, enum_cls) -> None:
    """Множество значений в CHECK равно множеству значений перечисления.

    Обе стороны: значение, которое база не примет, а Python предлагает,
    — это падение в рантайме; значение, которое база примет, а Python
    не знает, — это дыра в закрытом реестре.
    """
    sql = _check_sql(Base.metadata, table, column)
    in_db = set(part.strip().strip("'") for part in
                sql.split("IN (")[-1].split(")")[0].split(","))
    assert in_db == {member.value for member in enum_cls}


@pytest.mark.parametrize("table,column,enum_cls", REGISTRIES,
                         ids=lambda v: getattr(v, "__name__", str(v)))
def test_health_mirror_carries_the_same_registry(table, column, enum_cls) -> None:
    """`knowledge_semantic_runs` в health не зеркалится намеренно — у неё
    нет ни одного поля с содержимым источника; остальные обязаны нести
    тот же реестр."""
    if table == "knowledge_semantic_runs":
        assert f"health.{table}" not in HealthBase.metadata.tables
        return
    sql = _check_sql(HealthBase.metadata, table, column)
    in_db = set(part.strip().strip("'") for part in
                sql.split("IN (")[-1].split(")")[0].split(","))
    assert in_db == {member.value for member in enum_cls}


# ── база действительно отвергает неизвестное ─────────────────────────────

def _seed(session):
    """Минимум строк, чтобы дойти до проверяемой колонки: без источника
    и ревизии INSERT упадёт на внешнем ключе, а не на реестре, и тест
    доказал бы не то."""
    from helm_core.knowledge.ingest import ingest_text
    from helm_core.knowledge.tenancy import bind_knowledge_user
    from helm_core.models import KnowledgeSemanticRun, SemanticRunStatus as RS
    from conftest import SYSTEM_OWNER_ID

    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    source = ingest_text(session, domain="personal", text="документ для проверки реестра")
    session.flush()
    run = KnowledgeSemanticRun(
        knowledge_user_id=SYSTEM_OWNER_ID, source_id=source.id, semantic_version=2,
        status=RS.READY, windows_total=0, windows_processed=0, windows_failed=0,
        nodes_created=0, edges_created=0, unresolved_candidates=0,
    )
    session.add(run)
    session.flush()

    node_id, other_id = uuid.uuid4(), uuid.uuid4()
    for nid in (node_id, other_id):
        session.execute(text(
            "INSERT INTO knowledge_nodes (id, knowledge_user_id, kind, canonical_label, "
            "security_scope, status, semantic_run_id, created_at, updated_at) "
            "VALUES (:id, :u, 'entity', 'проверочная сущность', 'internal', 'active', "
            ":r, now(), now())"), {"id": nid, "u": SYSTEM_OWNER_ID, "r": run.id})
    session.flush()
    return {"source": source, "run": run, "node": node_id, "other": other_id,
            "user": SYSTEM_OWNER_ID}


_INSERTS = {
    ("knowledge_nodes", "kind"): (
        "INSERT INTO knowledge_nodes (id, knowledge_user_id, kind, canonical_label, "
        "security_scope, status, semantic_run_id, created_at, updated_at) "
        "VALUES (gen_random_uuid(), :user, :value, 'x', 'internal', 'active', :run, now(), now())"),
    ("knowledge_nodes", "status"): (
        "INSERT INTO knowledge_nodes (id, knowledge_user_id, kind, canonical_label, "
        "security_scope, status, semantic_run_id, created_at, updated_at) "
        "VALUES (gen_random_uuid(), :user, 'fact', 'x', 'internal', :value, :run, now(), now())"),
    ("knowledge_nodes", "date_precision"): (
        "INSERT INTO knowledge_nodes (id, knowledge_user_id, kind, canonical_label, "
        "security_scope, status, date_precision, semantic_run_id, created_at, updated_at) "
        "VALUES (gen_random_uuid(), :user, 'fact', 'x', 'internal', 'active', :value, "
        ":run, now(), now())"),
    ("knowledge_node_mentions", "evidence_type"): (
        "INSERT INTO knowledge_node_mentions (id, knowledge_user_id, node_id, source_id, "
        "evidence_type, semantic_run_id, created_at) "
        "VALUES (gen_random_uuid(), :user, :node, :source, :value, :run, now())"),
    ("knowledge_edges", "relation_type"): (
        "INSERT INTO knowledge_edges (id, knowledge_user_id, from_node_id, to_node_id, "
        "relation_type, evidence_type, status, semantic_run_id, created_at) "
        "VALUES (gen_random_uuid(), :user, :node, :other, :value, 'extracted', 'active', "
        ":run, now())"),
    ("knowledge_edges", "evidence_type"): (
        "INSERT INTO knowledge_edges (id, knowledge_user_id, from_node_id, to_node_id, "
        "relation_type, evidence_type, status, semantic_run_id, created_at) "
        "VALUES (gen_random_uuid(), :user, :node, :other, 'about', :value, 'active', "
        ":run, now())"),
    ("knowledge_edges", "status"): (
        "INSERT INTO knowledge_edges (id, knowledge_user_id, from_node_id, to_node_id, "
        "relation_type, evidence_type, status, semantic_run_id, created_at) "
        "VALUES (gen_random_uuid(), :user, :node, :other, 'about', 'extracted', :value, "
        ":run, now())"),
    ("knowledge_semantic_runs", "status"): (
        "INSERT INTO knowledge_semantic_runs (id, knowledge_user_id, source_id, "
        "semantic_version, status, windows_total, windows_processed, windows_failed, "
        "nodes_created, edges_created, unresolved_candidates, created_at) "
        "VALUES (gen_random_uuid(), :user, :source, 3, :value, 0, 0, 0, 0, 0, 0, now())"),
}


@pytest.mark.parametrize("table,column,enum_cls", REGISTRIES,
                         ids=lambda v: getattr(v, "__name__", str(v)))
def test_database_rejects_a_value_outside_the_registry(session, table, column, enum_cls) -> None:
    seed = _seed(session)
    params = {"user": seed["user"], "run": seed["run"].id, "source": seed["source"].id,
              "node": seed["node"], "other": seed["other"],
              # Короткое намеренно: `date_precision` это varchar(8), и
              # длинная строка упала бы на длине, не на реестре — тест
              # доказывал бы не то, что заявлено.
              "value": "нет"}

    with pytest.raises(sqlalchemy.exc.IntegrityError) as err:
        session.execute(text(_INSERTS[(table, column)]), params)
        session.flush()
    assert f"ck_{table}_{column}" in str(err.value)
    session.rollback()


@pytest.mark.parametrize("table,column,enum_cls", REGISTRIES,
                         ids=lambda v: getattr(v, "__name__", str(v)))
def test_database_accepts_every_value_of_the_registry(session, table, column, enum_cls) -> None:
    """Обратная сторона: слишком узкий CHECK — такая же ошибка, как
    слишком широкий, и обнаружилась бы уже на живом корпусе."""
    seed = _seed(session)
    for member in enum_cls:
        session.execute(text(_INSERTS[(table, column)]),
                        {"user": seed["user"], "run": seed["run"].id,
                         "source": seed["source"].id, "node": seed["node"],
                         "other": seed["other"], "value": member.value})
    session.flush()


# ── цикл semantic_run_id (§14.5 + решение 02.09.2026) ────────────────────

@pytest.mark.parametrize("kind", sorted(NODE_KINDS_WITHOUT_RUN))
def test_identity_nodes_may_have_no_run(session, kind) -> None:
    """ENTITY — личность, а не продукт прохода. DOCUMENT_REF/MEMORY_REF —
    личности существующего документа и существующей микро-памяти,
    решение 02.09.2026: их цикл тот же.

    `NOT NULL` для ENTITY не вводится намеренно — привязка личности к
    ревизии сделала бы её одноразовой и вернула бы дублирование, ради
    устранения которого semantic-v2 и заводится.
    """
    seed = _seed(session)
    session.execute(text(
        "INSERT INTO knowledge_nodes (id, knowledge_user_id, kind, canonical_label, "
        "security_scope, status, semantic_run_id, created_at, updated_at) "
        "VALUES (gen_random_uuid(), :u, :k, 'личность', 'internal', 'active', NULL, "
        "now(), now())"), {"u": seed["user"], "k": kind.value})
    session.flush()


@pytest.mark.parametrize("kind", ["event", "fact", "decision", "concept"])
def test_statement_nodes_require_a_run(session, kind) -> None:
    """Утверждение без ревизии — узел, который откат ревизии не найдёт и
    не уберёт."""
    seed = _seed(session)
    with pytest.raises(sqlalchemy.exc.IntegrityError) as err:
        session.execute(text(
            "INSERT INTO knowledge_nodes (id, knowledge_user_id, kind, canonical_label, "
            "security_scope, status, semantic_run_id, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :u, :k, 'утверждение', 'internal', 'active', NULL, "
            "now(), now())"), {"u": seed["user"], "k": kind})
        session.flush()
    assert "ck_knowledge_nodes_run_required_for_atoms" in str(err.value)
    session.rollback()


def test_mention_requires_a_run(session) -> None:
    """У упоминания исключений нет вовсе — поэтому NOT NULL, а не CHECK."""
    seed = _seed(session)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        session.execute(text(
            "INSERT INTO knowledge_node_mentions (id, knowledge_user_id, node_id, source_id, "
            "evidence_type, semantic_run_id, created_at) "
            "VALUES (gen_random_uuid(), :u, :n, :s, 'extracted', NULL, now())"),
            {"u": seed["user"], "n": seed["node"], "s": seed["source"].id})
        session.flush()
    session.rollback()


@pytest.mark.parametrize("evidence,allowed", [
    ("extracted", False), ("inferred", False), ("owner_explicit", True),
])
def test_edge_run_requirement_follows_evidence(session, evidence, allowed) -> None:
    """Связь, порождённую моделью, обязано быть чем откатить. Связь,
    написанную владельцем, откатывать нечем и незачем: прохода у неё
    нет по определению."""
    seed = _seed(session)
    statement = text(
        "INSERT INTO knowledge_edges (id, knowledge_user_id, from_node_id, to_node_id, "
        "relation_type, evidence_type, status, semantic_run_id, created_at) "
        "VALUES (gen_random_uuid(), :u, :a, :b, 'about', :e, 'active', NULL, now())")
    params = {"u": seed["user"], "a": seed["node"], "b": seed["other"], "e": evidence}

    if allowed:
        session.execute(statement, params)
        session.flush()
    else:
        with pytest.raises(sqlalchemy.exc.IntegrityError) as err:
            session.execute(statement, params)
            session.flush()
        assert "ck_knowledge_edges_run_required_for_derived" in str(err.value)
        session.rollback()
