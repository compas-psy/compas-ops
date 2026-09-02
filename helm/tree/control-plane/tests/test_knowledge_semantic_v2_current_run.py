"""v4.0 §14.5 — гейт текущей семантической ревизии, проверка в самой БД.

Правило спеки: «Only a revision whose run reached READY may become
`current_semantic_revision` for that source», и «Queries must never
observe half-written staging nodes from a RUNNING/FAILED backfill».

Тесты идут через SQLAlchemy, но проверяют СУБД, а не Python: ни одна из
шести ситуаций ниже не запрещена ни моделью, ни валидацией в коде — их
обязан отвергнуть сам Postgres. Поэтому каждая пишется прямым UPDATE, а
не через несуществующий пока сервис переключения ревизий (его напишет
R3, и он обязан упереться в эти же триггеры).

Таблица распоряжения владельца от 02.09.2026, все шесть строк:

    current→PENDING              DENIED
    current→FAILED               DENIED
    current→другой источник READY DENIED
    current→чужой тенант READY   DENIED
    current→свой READY           PASS
    current READY→FAILED         DENIED
"""

import uuid

import pytest
import sqlalchemy.exc
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (
    KnowledgeSemanticRun, KnowledgeSource, KnowledgeUser, KnowledgeUserRole, SemanticRunStatus,
)

from conftest import SYSTEM_OWNER_ID


def _run(session, *, source, status, user_id=None, version=2):
    run = KnowledgeSemanticRun(
        knowledge_user_id=user_id or source.knowledge_user_id, source_id=source.id,
        semantic_version=version, status=status, windows_total=1, windows_processed=1,
        windows_failed=0, nodes_created=0, edges_created=0, unresolved_candidates=0,
    )
    session.add(run)
    session.flush()
    return run


def _make_current(session, source, run):
    """Прямым UPDATE, минуя ORM: проверяется поведение базы, а не то,
    что делает SQLAlchemy по дороге."""
    session.execute(
        text("UPDATE knowledge_sources SET current_semantic_run_id = :r WHERE id = :s"),
        {"r": run.id, "s": source.id},
    )


@pytest.fixture
def owner_source(session):
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    source = ingest_text(session, domain="personal", text="исходный документ владельца")
    session.flush()
    return source


# ── пять строк про назначение текущей ревизии ────────────────────────────

@pytest.mark.parametrize("status", [
    SemanticRunStatus.PENDING, SemanticRunStatus.RUNNING,
    SemanticRunStatus.DEGRADED, SemanticRunStatus.FAILED,
])
def test_current_run_must_be_ready(session, owner_source, status):
    """DEGRADED и RUNNING сюда добавлены сверх списка владельца: они из
    того же класса «проход не дошёл до конца», и пропустить их значило
    бы показать ответ по частично разобранному документу."""
    run = _run(session, source=owner_source, status=status)
    with pytest.raises(sqlalchemy.exc.DatabaseError) as err:
        _make_current(session, owner_source, run)
    assert "ready" in str(err.value).lower()
    session.rollback()


def test_current_run_of_another_source_is_denied(session, owner_source):
    other = ingest_text(session, domain="personal", text="другой документ того же владельца")
    session.flush()
    run_of_other = _run(session, source=other, status=SemanticRunStatus.READY)

    with pytest.raises(sqlalchemy.exc.DatabaseError):
        _make_current(session, owner_source, run_of_other)
    session.rollback()


def test_current_run_of_another_tenant_is_denied(engine, session, owner_source):
    """Чужая ревизия не видна под RLS — гейт падает на «не нашёл», и это
    правильный отказ: тенантность здесь не дублируется отдельной
    проверкой внутри триггера, она уже обеспечена политикой."""
    # id снимаются ДО commit: `bind_knowledge_user()` ставит GUC через
    # SET LOCAL, коммит его сбрасывает, и обращение к атрибуту объекта
    # после коммита пойдёт за строкой уже без тенанта — под RLS её не
    # видно, и SQLAlchemy сочтёт её удалённой.
    owner_source_id = owner_source.id
    other_user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(other_user)
    session.flush()
    other_user_id = other_user.id
    session.commit()

    with Session(engine) as their_session:
        bind_knowledge_user(their_session, other_user_id)
        their_source = ingest_text(their_session, domain="personal", text="документ соседа",
                                   knowledge_user_id=other_user_id)
        their_session.flush()
        their_run = _run(their_session, source=their_source, status=SemanticRunStatus.READY)
        their_run_id = their_run.id
        their_session.commit()

    with Session(engine) as owner_session:
        bind_knowledge_user(owner_session, SYSTEM_OWNER_ID)
        with pytest.raises(sqlalchemy.exc.DatabaseError):
            owner_session.execute(
                text("UPDATE knowledge_sources SET current_semantic_run_id = :r WHERE id = :s"),
                {"r": their_run_id, "s": owner_source_id},
            )
        owner_session.rollback()


def test_current_run_own_ready_is_allowed(session, owner_source):
    run = _run(session, source=owner_source, status=SemanticRunStatus.READY)
    _make_current(session, owner_source, run)
    session.flush()

    assert session.scalar(select(KnowledgeSource.current_semantic_run_id).where(
        KnowledgeSource.id == owner_source.id)) == run.id


def test_clearing_the_pointer_is_always_allowed(session, owner_source):
    """NULL — законное состояние: до R8 граф v2 не построен ни для
    одного источника, и «ревизии ещё нет» не должно быть ошибкой."""
    run = _run(session, source=owner_source, status=SemanticRunStatus.READY)
    _make_current(session, owner_source, run)
    session.flush()
    session.execute(
        text("UPDATE knowledge_sources SET current_semantic_run_id = NULL WHERE id = :s"),
        {"s": owner_source.id})
    session.flush()

    assert session.scalar(select(KnowledgeSource.current_semantic_run_id).where(
        KnowledgeSource.id == owner_source.id)) is None


# ── шестая строка: ревизию нельзя испортить, пока она текущая ────────────

@pytest.mark.parametrize("status", [
    SemanticRunStatus.FAILED, SemanticRunStatus.PENDING, SemanticRunStatus.DEGRADED,
])
def test_current_run_cannot_leave_ready(session, owner_source, status):
    """Без этого триггера пропуск был бы полным: назначить READY, потом
    перевести в FAILED — и источник указывает на провалившийся проход,
    а первая проверка об этом никогда не узнает."""
    run = _run(session, source=owner_source, status=SemanticRunStatus.READY)
    _make_current(session, owner_source, run)
    session.flush()

    with pytest.raises(sqlalchemy.exc.DatabaseError) as err:
        session.execute(
            text("UPDATE knowledge_semantic_runs SET status = :st WHERE id = :r"),
            {"st": status.value, "r": run.id})
    assert "текущей" in str(err.value)
    session.rollback()


def test_current_run_cannot_be_moved_to_another_source(session, owner_source):
    other = ingest_text(session, domain="personal", text="ещё один документ")
    session.flush()
    run = _run(session, source=owner_source, status=SemanticRunStatus.READY)
    _make_current(session, owner_source, run)
    session.flush()

    with pytest.raises(sqlalchemy.exc.DatabaseError):
        session.execute(
            text("UPDATE knowledge_semantic_runs SET source_id = :s WHERE id = :r"),
            {"s": other.id, "r": run.id})
    session.rollback()


def test_not_current_run_may_change_status_freely(session, owner_source):
    """Гейт не должен мешать обычной работе: ревизия, которая не
    назначена текущей, живёт своим циклом PENDING→RUNNING→READY/FAILED
    без единого возражения."""
    run = _run(session, source=owner_source, status=SemanticRunStatus.PENDING)
    for status in (SemanticRunStatus.RUNNING, SemanticRunStatus.FAILED,
                   SemanticRunStatus.READY, SemanticRunStatus.DEGRADED):
        session.execute(
            text("UPDATE knowledge_semantic_runs SET status = :st WHERE id = :r"),
            {"st": status.value, "r": run.id})
    session.flush()

    assert session.scalar(select(KnowledgeSemanticRun.status).where(
        KnowledgeSemanticRun.id == run.id)) == SemanticRunStatus.DEGRADED


def test_counters_of_a_current_run_may_still_be_updated(session, owner_source):
    """Триггер срабатывает только на три поля, от которых зависит
    пригодность. Счётчики окон правятся на каждом шаге разбора — если
    бы гейт трогал и их, он ломал бы саму работу, которую охраняет."""
    run = _run(session, source=owner_source, status=SemanticRunStatus.READY)
    _make_current(session, owner_source, run)
    session.flush()

    session.execute(
        text("UPDATE knowledge_semantic_runs SET windows_processed = 42 WHERE id = :r"),
        {"r": run.id})
    session.flush()

    assert session.scalar(select(KnowledgeSemanticRun.windows_processed).where(
        KnowledgeSemanticRun.id == run.id)) == 42
