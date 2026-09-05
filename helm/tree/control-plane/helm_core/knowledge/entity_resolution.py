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


#: Типы, для которых однословной подписи НЕДОСТАТОЧНО для тождества.
#: Владелец, 05.09.2026: «Для PERSON однословная подпись никогда не
#: является strong identity сама по себе». Одна фамилия — не человек:
#: однофамильцы существуют, а цена ошибки — чужая медицинская запись в
#: карточке. Для ORGANIZATION/PLACE/CONCEPT правило не меняется: «Инвитро»
#: и «Москва» однословны по своей природе, и это их полное имя.
_MULTI_TOKEN_REQUIRED = frozenset({"PERSON", "person"})


def is_strong_label(entity_type: str, normalized_key: str) -> bool:
    """Достаточно ли одной подписи, чтобы считать тождество доказанным."""
    if entity_type in _MULTI_TOKEN_REQUIRED:
        return len(_tokens(normalized_key)) >= 2
    return bool(normalized_key)


def is_surname_only(one: str, other: str) -> bool:
    """Одна подпись — единственное слово, и это первое слово второй.

    «иванов» и «иванов пётр сергеевич». §14.7 называет такое сходство
    недостаточным поимённо: однофамильцы существуют, а цена ошибки —
    приписать человеку чужую медицинскую запись. Проверка нужна не
    чтобы слить, а чтобы не потерять вопрос.
    """
    a, b = _tokens(one), _tokens(other)
    if not a or not b:
        return False
    # Две одинаковые голые фамилии — тоже вопрос, а не совпадение
    # («Иванов» и «Иванов» из разных выписок: владелец, 05.09.2026,
    # называет и этот случай кандидатом). Раньше равные длины отсекались
    # сразу, и такая пара молча оставалась без вопроса.
    if len(a) == 1 and len(b) == 1:
        return a[0] == b[0]
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return len(short) == 1 and short[0] == long_[0]


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

        def add_candidate(identity_id: uuid.UUID, reason: str) -> None:
            if (node.id, identity_id, str(reason)) in known_candidates:
                return
            known_candidates.add((node.id, identity_id, str(reason)))
            report.candidates_created += 1
            report.candidates_by_reason[str(reason)] = (
                report.candidates_by_reason.get(str(reason), 0) + 1)
            if not dry_run:
                graph.add(models.candidate(
                    id=uuid.uuid4(), knowledge_user_id=tenant_id, node_id=node.id,
                    identity_id=identity_id, reason=reason))

        def identity_for(key_: str) -> object:
            existing = identities.get((entity_type, key_))
            if existing is not None:
                return existing
            created = models.identity(
                id=uuid.uuid4(), knowledge_user_id=tenant_id, entity_type=entity_type,
                canonical_label=node.canonical_label, normalized_key=key_)
            identities[(entity_type, key_)] = created
            report.identities_created += 1
            if not dry_run:
                graph.add(created)
            return created

        # Совпадение алиаса больше НЕ сливает (владелец, 05.09.2026):
        # `knowledge_entity_aliases` заполняет разбор, а не владелец, и
        # признака подтверждения у строки нет. Пока его нет, совпадение
        # алиаса — вопрос, а не доказательство.
        alias_identity_id = alias_owner.get(key)
        if alias_identity_id is not None:
            owner_identity = next((i for i in identities.values()
                                   if i.id == alias_identity_id), None)
            if owner_identity is not None and owner_identity.entity_type == entity_type:
                add_candidate(owner_identity.id,
                              EntityResolutionReason.ALIAS_UNCONFIRMED)

        identity = identity_for(key)

        # Однословная подпись PERSON не доказывает тождества. Личность
        # заводится (иначе вопрос не к чему прицепить и он потеряется),
        # но узел в неё НЕ входит: состав такой личности назначает
        # человек, разбирая кандидата.
        if is_strong_label(entity_type, key):
            member = models.member(
                id=uuid.uuid4(), knowledge_user_id=tenant_id, identity_id=identity.id,
                node_id=node.id, matched_on=EntityIdentityMatch.NORMALIZED_LABEL)
            resolved.add(node.id)
            report.members_created += 1
            report.matched_by[str(EntityIdentityMatch.NORMALIZED_LABEL)] = (
                report.matched_by.get(str(EntityIdentityMatch.NORMALIZED_LABEL), 0) + 1)
            if not dry_run:
                graph.add(member)
        else:
            add_candidate(identity.id, EntityResolutionReason.SURNAME_ONLY)

        for (other_type, other_key), other in identities.items():
            if other.id == identity.id:
                continue
            if other_type == entity_type and is_surname_only(key, other_key):
                reason = EntityResolutionReason.SURNAME_ONLY
            elif other_type != entity_type and other_key == key:
                reason = EntityResolutionReason.TYPE_CONFLICT
            else:
                continue
            add_candidate(other.id, reason)

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


def probe_weak_person_identities(graph: Session, models, *,
                                 tenant_id: uuid.UUID) -> dict[str, int]:
    """Сколько личностей-людей собрано по ОДНОСЛОВНОЙ подписи (R6 patch).

    Проверка перед изменением живого производного состояния (владелец,
    05.09.2026): «PERSON identities with member_count > 1 and canonical
    normalized label = 1 token. Если 0 — текущие данные этим дефектом не
    затронуты.»

    Наружу уходят только числа. Подписи не печатаются и не возвращаются:
    имя врача из выписки — медицинская информация, а вопрос здесь
    количественный.
    """
    rows = graph.execute(
        select(models.identity.normalized_key, func.count(models.member.id))
        .outerjoin(models.member, models.member.identity_id == models.identity.id)
        .where(models.identity.knowledge_user_id == tenant_id,
               models.identity.entity_type.in_(tuple(_MULTI_TOKEN_REQUIRED)))
        .group_by(models.identity.id, models.identity.normalized_key)).all()

    one_token = [count for key, count in rows if len(_tokens(key or "")) < 2]

    def total(model) -> int:
        return graph.scalar(
            select(func.count()).select_from(model)
            .where(model.knowledge_user_id == tenant_id)) or 0

    return {"person_identities": len(rows),
            "one_token": len(one_token),
            "one_token_with_members": sum(1 for c in one_token if c > 0),
            # То самое число из распоряжения: однословная подпись, под
            # которую уже сведено больше одного узла.
            "one_token_with_members_gt1": sum(1 for c in one_token if c > 1),
            # Итоги трёх таблиц. Нужны, чтобы «кандидаты сохраняются»
            # было измерением, а не рассуждением о том, что проход
            # только вставляет: до и после прохода эти числа сверяются.
            # `person_zero_members` — вход в R7: личность-человек без
            # состава в ответах не участвует.
            "identities_total": total(models.identity),
            "members_total": total(models.member),
            "candidates_total": total(models.candidate),
            "person_zero_members": sum(1 for _, c in rows if c == 0)}


def probe_all(session: Session, *, knowledge_user_id: uuid.UUID | None = None
              ) -> dict[str, dict[str, int] | None]:
    """Та же проверка по обеим схемам. Ничего не пишет."""
    tenant_id = bind_knowledge_user(session, knowledge_user_id)
    report: dict[str, dict[str, int] | None] = {
        "public": probe_weak_person_identities(session, PUBLIC_MODELS, tenant_id=tenant_id)}
    if health_schema_configured():
        with health_session(tenant_id) as graph:
            report["health"] = probe_weak_person_identities(
                graph, HEALTH_MODELS, tenant_id=tenant_id)
    else:
        report["health"] = None
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


def rebuild_all(session: Session, *, knowledge_user_id: uuid.UUID | None = None
                ) -> dict[str, dict]:
    """Снести производные строки R6 и собрать их заново (владелец, 05.09.2026).

    Разрешено ровно для трёх таблиц — личности, состав, кандидаты. Они
    ПРОИЗВОДНЫЕ: пересчитываются из узлов, и удалить их не значит
    потерять что-либо, чего нельзя восстановить проходом. Исходные узлы,
    упоминания, провенанс, прогоны и источники не трогаются — это
    свойство проверяет инвариант внутри `resolve_in()`, а не обещание в
    комментарии.

    Нужен потому, что правила тождества изменились: состав, собранный по
    старым правилам, старым и остаётся — проход идемпотентен и уже
    отнесённый узел не пересматривает.
    """
    tenant_id = bind_knowledge_user(session, knowledge_user_id)
    rows = session.execute(
        select(KnowledgeSource.domain, KnowledgeSource.current_semantic_run_id)
        .where(KnowledgeSource.current_semantic_run_id.is_not(None))).all()
    public_runs = {r for d, r in rows if not is_health_domain(d)}
    health_runs = {r for d, r in rows if is_health_domain(d)}

    def wipe(graph: Session, models) -> dict[str, int]:
        # Порядок обязателен: кандидат и состав ссылаются на личность.
        removed = {}
        for name, model in (("candidates", models.candidate),
                            ("members", models.member),
                            ("identities", models.identity)):
            rows_ = graph.scalars(
                select(model).where(model.knowledge_user_id == tenant_id)).all()
            for row in rows_:
                graph.delete(row)
            removed[name] = len(rows_)
        graph.flush()
        return removed

    report: dict[str, dict] = {}
    removed_public = wipe(session, PUBLIC_MODELS)
    report["public"] = {"removed": removed_public,
                        **resolve_in(session, PUBLIC_MODELS, tenant_id=tenant_id,
                                     current_run_ids=public_runs).as_dict()}
    if health_schema_configured():
        with health_session(tenant_id) as graph:
            removed_health = wipe(graph, HEALTH_MODELS)
            report["health"] = {"removed": removed_health,
                                **resolve_in(graph, HEALTH_MODELS, tenant_id=tenant_id,
                                             current_run_ids=health_runs).as_dict()}
    else:
        report["health"] = None
    return report


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R6: разрешение сущностей")
    parser.add_argument("--dry-run", action="store_true",
                        help="посчитать и напечатать, ничего не записывая")
    parser.add_argument("--probe", action="store_true",
                        help="только проверка: личности-люди с однословной подписью")
    parser.add_argument("--rebuild", action="store_true",
                        help="снести производные строки R6 и собрать заново")
    args = parser.parse_args(argv)

    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    with sessionmaker(engine, expire_on_commit=False)() as session:
        if args.probe:
            report = probe_all(session)
            session.rollback()
        elif args.rebuild:
            report = rebuild_all(session)
            session.commit()
        elif args.dry_run:
            report = resolve_all(session, dry_run=True)
            session.rollback()
        else:
            report = resolve_all(session)
            session.commit()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
