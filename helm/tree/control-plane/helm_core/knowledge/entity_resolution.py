"""R6 — разрешение сущностей: кто здесь одно и то же лицо.

Распоряжение владельца 05.09.2026: «R6 — identity only... Auto-resolution
только по strong identity: same tenant, same entity_type, exact
normalized label / exact known alias. Fuzzy/surname-only merge запрещён.
Исходные nodes/mentions/provenance не мутировать и не удалять.»

Отсюда вся конструкция.

**Ничего не сливается разрушительно.** Узел прогона остаётся ровно тем,
чем его записал разбор; принадлежность личности лежит отдельной строкой
(`KnowledgeEntityIdentityMember`). Удаление этих строк возвращает граф в
прежнее состояние, ничего не восстанавливая, — то есть решение
обратимо, а не «обратимо в принципе».

**Сильное тождество — ровно два случая.** Дословное совпадение
нормализованной подписи при том же `entity_type` и дословное совпадение
с ПОДТВЕРЖДЁННЫМ алиасом (`knowledge_entity_aliases`). Больше ничего:
ни расстояний, ни векторов, ни «скорее всего это он».

**Похоже — не значит слить.** «Иванов» рядом с «Иванов Пётр Сергеевич»
и одинаковая подпись при разных типах записываются кандидатом
(`KnowledgeEntityResolutionCandidate`) — вопросом к человеку, а не
решением агента (устав §6). Кандидат существует затем, чтобы «не слили»
не означало «потеряли».

Разбираются только узлы ТЕКУЩИХ ревизий источников (§14.20): узлы
брошенной или деградировавшей ревизии в графе есть, но текущим ответом
не являются, и личность из них строить нельзя.
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass, field

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from .health_schema import health_schema_configured, health_session, is_health_domain
from .semantic_publish import HEALTH_MODELS, PUBLIC_MODELS
from .tenancy import bind_knowledge_user
from ..config import get_settings
from ..models import KnowledgeSource
from ..models.base import (
    EntityIdentityMatch, EntityResolutionReason, SemanticNodeKind, SemanticNodeStatus,
)


@dataclass
class ResolutionReport:
    """Сводка прохода. Только числа: подписи сущностей — содержимое."""

    #: Все узлы тенанта в этой схеме — до и после прохода. Служит
    #: доказательством того, что исходные данные не тронуты; сравнение
    #: делает сам проход, а не читатель лога.
    nodes_total: int = 0
    nodes_seen: int = 0
    already_resolved: int = 0
    identities_created: int = 0
    members_created: int = 0
    candidates_created: int = 0
    matched_by: dict[str, int] = field(default_factory=dict)
    candidates_by_reason: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"nodes_total": self.nodes_total,
                "nodes_seen": self.nodes_seen,
                "already_resolved": self.already_resolved,
                "identities_created": self.identities_created,
                "members_created": self.members_created,
                "candidates_created": self.candidates_created,
                "matched_by": self.matched_by,
                "candidates_by_reason": self.candidates_by_reason}


def _tokens(normalized_key: str) -> list[str]:
    return normalized_key.split()


def is_surname_only(one: str, other: str) -> bool:
    """Одна подпись — единственное слово, и это первое слово второй.

    «иванов» и «иванов пётр сергеевич». §14.7 называет такое сходство
    недостаточным поимённо: однофамильцы существуют, а цена ошибки —
    приписать человеку чужую медицинскую запись. Проверка нужна не
    чтобы слить, а чтобы не потерять вопрос.
    """
    a, b = _tokens(one), _tokens(other)
    if len(a) == len(b):
        return False
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return len(short) == 1 and bool(long_) and short[0] == long_[0]


def _alias_owner(graph: Session, models, tenant_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Нормализованный алиас → личность, за которой он подтверждён.

    Алиас привязан к УЗЛУ, а личность — к набору узлов, поэтому владелец
    алиаса определяется через состав. Алиас, доставшийся двум личностям
    сразу, исключается: он тогда ничего не доказывает, и молча выбрать
    первую попавшуюся значило бы сделать слияние по совпадению.
    """
    rows = graph.execute(
        select(models.alias.normalized_alias, models.member.identity_id)
        .join(models.member, models.member.node_id == models.alias.entity_node_id)
        .where(models.alias.knowledge_user_id == tenant_id)).all()
    owners: dict[str, set[uuid.UUID]] = {}
    for alias, identity_id in rows:
        owners.setdefault(alias, set()).add(identity_id)
    return {alias: next(iter(ids)) for alias, ids in owners.items() if len(ids) == 1}


def resolve_in(graph: Session, models, *, tenant_id: uuid.UUID,
               current_run_ids: set[uuid.UUID], dry_run: bool = False) -> ResolutionReport:
    """Один проход разрешения в одной схеме (public или health-зеркало).

    Идемпотентен: узел, уже отнесённый к личности, не трогается повторно
    — иначе состав личности удваивался бы с каждым прогоном, а «сколько
    документов про этого врача» стало бы неверным числом.
    """
    def count_nodes() -> int:
        return graph.scalar(
            select(func.count()).select_from(models.node)
            .where(models.node.knowledge_user_id == tenant_id)) or 0

    report = ResolutionReport(nodes_total=count_nodes())
    if not current_run_ids:
        return report

    resolved = set(graph.scalars(
        select(models.member.node_id)
        .where(models.member.knowledge_user_id == tenant_id)).all())
    identities: dict[tuple[str, str], object] = {
        (row.entity_type, row.normalized_key): row
        for row in graph.scalars(
            select(models.identity)
            .where(models.identity.knowledge_user_id == tenant_id)).all()
    }
    alias_owner = _alias_owner(graph, models, tenant_id)
    known_candidates = {
        (node_id, identity_id, reason) for node_id, identity_id, reason in graph.execute(
            select(models.candidate.node_id, models.candidate.identity_id,
                   models.candidate.reason)
            .where(models.candidate.knowledge_user_id == tenant_id)).all()
    }

    # Порядок детерминированный: повтор прохода на тех же данных даёт те
    # же личности с теми же идентификаторами-ролями, и «первая подпись
    # становится канонической» — правило, а не случайность порядка строк.
    nodes = graph.scalars(
        select(models.node)
        .where(models.node.knowledge_user_id == tenant_id,
               models.node.kind == SemanticNodeKind.ENTITY,
               models.node.status == SemanticNodeStatus.ACTIVE,
               models.node.semantic_run_id.in_(current_run_ids))
        .order_by(models.node.created_at, models.node.id)).all()

    for node in nodes:
        report.nodes_seen += 1
        if node.id in resolved:
            report.already_resolved += 1
            continue
        entity_type = node.entity_type or ""
        key = node.normalized_key or ""
        if not entity_type or not key:
            # Сущность без типа или без ключа — не личность, а дефект
            # записи. Заводить для неё канонический узел значило бы
            # закрепить дефект; пропуск виден по nodes_seen.
            continue

        identity = identities.get((entity_type, key))
        matched_on = EntityIdentityMatch.NORMALIZED_LABEL
        if identity is None and key in alias_owner:
            identity = next((i for i in identities.values()
                             if i.id == alias_owner[key]), None)
            if identity is not None and identity.entity_type == entity_type:
                matched_on = EntityIdentityMatch.ALIAS
            else:
                # Алиас чужого типа — не доказательство (§14.7).
                identity = None

        if identity is None:
            identity = models.identity(
                id=uuid.uuid4(), knowledge_user_id=tenant_id, entity_type=entity_type,
                canonical_label=node.canonical_label, normalized_key=key)
            identities[(entity_type, key)] = identity
            report.identities_created += 1
            if not dry_run:
                graph.add(identity)

        member = models.member(
            id=uuid.uuid4(), knowledge_user_id=tenant_id, identity_id=identity.id,
            node_id=node.id, matched_on=matched_on)
        resolved.add(node.id)
        report.members_created += 1
        report.matched_by[str(matched_on)] = report.matched_by.get(str(matched_on), 0) + 1
        if not dry_run:
            graph.add(member)

        for (other_type, other_key), other in identities.items():
            if other.id == identity.id:
                continue
            if other_type == entity_type and is_surname_only(key, other_key):
                reason = EntityResolutionReason.SURNAME_ONLY
            elif other_type != entity_type and other_key == key:
                reason = EntityResolutionReason.TYPE_CONFLICT
            else:
                continue
            if (node.id, other.id, str(reason)) in known_candidates:
                continue
            known_candidates.add((node.id, other.id, str(reason)))
            report.candidates_created += 1
            report.candidates_by_reason[str(reason)] = (
                report.candidates_by_reason.get(str(reason), 0) + 1)
            if not dry_run:
                graph.add(models.candidate(
                    id=uuid.uuid4(), knowledge_user_id=tenant_id, node_id=node.id,
                    identity_id=other.id, reason=reason))

    if not dry_run:
        graph.flush()

    # Инвариант, а не пожелание: «исходные nodes/mentions/provenance не
    # мутировать и не удалять» (владелец, 05.09.2026). Проверяется здесь,
    # в той же схеме и под тем же RLS, потому что снаружи — нельзя:
    # health-узлы лежат в зеркале, и счётчик в public показал бы ноль,
    # ничего при этом не проверив (найдено прогоном 271).
    after = count_nodes()
    if after != report.nodes_total:
        raise RuntimeError(
            f"разрешение сущностей изменило число узлов: "
            f"{report.nodes_total} -> {after}")
    return report


def resolve_all(session: Session, *, knowledge_user_id: uuid.UUID | None = None,
                dry_run: bool = False) -> dict[str, dict]:
    """Проход по обеим схемам: public и health-зеркало.

    Разделение по домену — то же решение, что у публикации
    (`semantic_publish`): health-источник писал узлы в зеркало отдельной
    ролью, значит и личности его сущностей живут там же. Считать их в
    public было бы не «удобнее», а выносом имени врача из health.
    """
    tenant_id = bind_knowledge_user(session, knowledge_user_id)
    rows = session.execute(
        select(KnowledgeSource.domain, KnowledgeSource.current_semantic_run_id)
        .where(KnowledgeSource.current_semantic_run_id.is_not(None))).all()

    public_runs = {run_id for domain, run_id in rows if not is_health_domain(domain)}
    health_runs = {run_id for domain, run_id in rows if is_health_domain(domain)}

    report = {"public": resolve_in(session, PUBLIC_MODELS, tenant_id=tenant_id,
                                   current_run_ids=public_runs,
                                   dry_run=dry_run).as_dict()}
    if health_runs and health_schema_configured():
        with health_session(tenant_id) as graph:
            # `health_session` коммитит на успешном выходе. При сухом
            # прогоне коммитить нечего: `resolve_in(dry_run=True)` не
            # добавляет ни строки — это свойство закреплено тестом,
            # а не соглашением.
            report["health"] = resolve_in(graph, HEALTH_MODELS, tenant_id=tenant_id,
                                          current_run_ids=health_runs,
                                          dry_run=dry_run).as_dict()
    else:
        report["health"] = None
    return report


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R6: разрешение сущностей")
    parser.add_argument("--dry-run", action="store_true",
                        help="посчитать и напечатать, ничего не записывая")
    args = parser.parse_args(argv)

    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    with sessionmaker(engine, expire_on_commit=False)() as session:
        report = resolve_all(session, dry_run=args.dry_run)
        if args.dry_run:
            session.rollback()
        else:
            session.commit()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
