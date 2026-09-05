"""R8 — перенос всего корпуса на semantic-v2.

`HELM_FINAL_v4.0_RESCUE §R8`: «Only after R4–R7 PASS. Idempotent,
resumable, per-source semantic revision. Preserve RAW/L1.»

Все четыре требования держатся не обещанием, а устройством.

**Идемпотентность.** Источник, у которого текущая ревизия уже
semantic-v2 и READY, пропускается. Повтор прогона на разобранном
корпусе не делает ничего и не тратит ни секунды модели.

**Возобновляемость.** Каждый источник — своя транзакция со своим
коммитом. Прогон, оборванный на середине, оставляет разобранное
разобранным; следующий продолжает с того же места, потому что
пропускает уже готовое (см. выше), а не потому что где-то хранит
курсор.

**Ревизия на источник.** Переключением `current_semantic_run_id`
занимается `publish_semantic_run` и только он: неготовая ревизия
текущей не становится, а прошлая живёт, пока замена не прошла проверку
(§14.20).

**RAW/L1 не трогаются.** Здесь нет ни одной записи в источники, чанки и
эмбеддинги: разбор только добавляет узлы новой ревизии. Поиск по L1
работает всё время, пока идёт перенос.

Бюджет времени — не украшение. Разбор идёт локальной моделью на том же
сервере, где живёт продукт, и полный корпус это часы. `--budget-seconds`
режет прогон на куски, каждый из которых укладывается в один запуск
Actions; проверка бюджета стоит ПЕРЕД началом источника, а не внутри —
источник, начатый разбором, доводится до конца, иначе ревизия осталась
бы недописанной.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from .semantic_pilot import source_text
from .semantic_publish import publish_semantic_run
from .tenancy import bind_knowledge_user
from ..config import get_settings
from ..models import KnowledgeSemanticRun, KnowledgeSource
from ..models.base import KnowledgeStatus, SemanticRunStatus

#: Версия разбора, которую R8 переносит. Задаётся здесь и передаётся в
#: публикацию явно: «что считается готовым» и «что публикуется» обязаны
#: быть одним числом, иначе прогон однажды начнёт считать готовым то,
#: чего не делал.
SEMANTIC_VERSION = 2


@dataclass
class SourceOutcome:
    source_id: str
    domain: str
    chars: int
    status: str
    windows_total: int = 0
    windows_failed: int = 0
    nodes: int = 0
    edges: int = 0
    coverage: float = 0.0
    seconds: float = 0.0
    switched: bool = False
    #: Имя класса исключения, если разбор упал. Только имя: текст ошибки
    #: модели может содержать кусок документа, а он медицинский (§5.2).
    error: str | None = None


@dataclass
class BackfillReport:
    sources_total: int = 0
    already_current: int = 0
    todo: int = 0
    processed: int = 0
    ready: int = 0
    degraded: int = 0
    failed: int = 0
    no_text: int = 0
    stopped_by: str | None = None
    seconds: float = 0.0
    by_domain: dict[str, int] = field(default_factory=dict)
    outcomes: list[SourceOutcome] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"sources_total": self.sources_total,
                "already_current": self.already_current,
                "todo": self.todo, "processed": self.processed,
                "ready": self.ready, "degraded": self.degraded,
                "failed": self.failed, "no_text": self.no_text,
                "stopped_by": self.stopped_by, "seconds": round(self.seconds, 1),
                "by_domain": self.by_domain,
                "outcomes": [vars(o) for o in self.outcomes]}


def _is_current(session: Session, source: KnowledgeSource) -> bool:
    """Разобран ли источник ТЕКУЩЕЙ версией и годной ревизией."""
    if not source.current_semantic_run_id:
        return False
    run = session.get(KnowledgeSemanticRun, source.current_semantic_run_id)
    return (run is not None
            and run.semantic_version == SEMANTIC_VERSION
            and run.status == SemanticRunStatus.READY)


def _live_sources(session: Session,
                  domains: tuple[str, ...] | None = None) -> list[KnowledgeSource]:
    query = select(KnowledgeSource).where(KnowledgeSource.status == KnowledgeStatus.ACTIVE)
    if domains:
        query = query.where(KnowledgeSource.domain.in_(domains))
    # Порядок детерминированный: оборванный прогон продолжается с того
    # же места, а два прогона подряд идут по одному и тому же списку.
    return list(session.scalars(query.order_by(
        KnowledgeSource.domain, KnowledgeSource.created_at, KnowledgeSource.id)).all())


def plan(session: Session, *, domains: tuple[str, ...] | None = None,
         knowledge_user_id: uuid.UUID | None = None) -> dict:
    """Сколько работы осталось. Ничего не пишет и модель не трогает.

    Считает той же выборкой и тем же условием готовности, что и сам
    перенос: план, посчитанный отдельной логикой, обещал бы не то, что
    произойдёт.
    """
    bind_knowledge_user(session, knowledge_user_id)
    sources = _live_sources(session, domains)
    todo_chars = 0
    todo_by_domain: dict[str, int] = {}
    done = 0
    no_text = 0
    for source in sources:
        if _is_current(session, source):
            done += 1
            continue
        text = source_text(source)
        if text is None:
            no_text += 1
            continue
        todo_chars += len(text)
        todo_by_domain[source.domain] = todo_by_domain.get(source.domain, 0) + 1
    return {"sources_total": len(sources), "already_current": done,
            "todo": sum(todo_by_domain.values()), "no_text": no_text,
            "todo_chars": todo_chars, "todo_by_domain": todo_by_domain}


def run_backfill(session: Session, *, limit: int | None = None,
                 budget_seconds: float | None = None,
                 domains: tuple[str, ...] | None = None,
                 knowledge_user_id: uuid.UUID | None = None) -> BackfillReport:
    bind_knowledge_user(session, knowledge_user_id)
    started = time.monotonic()
    sources = _live_sources(session, domains)
    report = BackfillReport(sources_total=len(sources))

    for source in sources:
        if _is_current(session, source):
            report.already_current += 1
            continue
        report.todo += 1
        if limit is not None and report.processed >= limit:
            report.stopped_by = report.stopped_by or "limit"
            continue
        # Бюджет проверяется ПЕРЕД источником: начатый разбор доводится
        # до конца, иначе ревизия осталась бы недописанной.
        if budget_seconds is not None and time.monotonic() - started >= budget_seconds:
            report.stopped_by = report.stopped_by or "budget"
            continue

        text = source_text(source)
        if text is None:
            report.no_text += 1
            report.outcomes.append(SourceOutcome(
                source_id=str(source.id), domain=source.domain, chars=0,
                status="нет текста"))
            continue

        at = time.monotonic()
        try:
            result = publish_semantic_run(session, source=source, text=text,
                                          semantic_version=SEMANTIC_VERSION)
            session.commit()
        except Exception as exc:  # noqa: BLE001 — падение одного источника не рушит перенос
            session.rollback()
            report.failed += 1
            report.outcomes.append(SourceOutcome(
                source_id=str(source.id), domain=source.domain, chars=len(text),
                status="исключение", seconds=round(time.monotonic() - at, 1),
                error=type(exc).__name__))
            continue

        report.processed += 1
        report.by_domain[source.domain] = report.by_domain.get(source.domain, 0) + 1
        if result.status == SemanticRunStatus.READY:
            report.ready += 1
        elif result.status == SemanticRunStatus.DEGRADED:
            report.degraded += 1
        else:
            report.failed += 1
        report.outcomes.append(SourceOutcome(
            source_id=str(source.id), domain=source.domain, chars=len(text),
            status=str(result.status), windows_total=result.windows_total,
            windows_failed=result.windows_failed, nodes=result.nodes_created,
            edges=result.edges_created, coverage=result.coverage_ratio,
            seconds=round(time.monotonic() - at, 1), switched=result.switched))

    report.seconds = time.monotonic() - started
    return report


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R8: перенос корпуса на semantic-v2")
    parser.add_argument("--plan", action="store_true",
                        help="посчитать оставшуюся работу, ничего не разбирая")
    parser.add_argument("--limit", type=int, help="разобрать не больше стольких источников")
    parser.add_argument("--budget-seconds", type=float,
                        help="не начинать новый источник после этого времени")
    parser.add_argument("--domain", action="append", dest="domains",
                        help="ограничить доменом (можно повторять)")
    args = parser.parse_args(argv)

    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    domains = tuple(args.domains) if args.domains else None
    with sessionmaker(engine, expire_on_commit=False)() as session:
        if args.plan:
            payload = plan(session, domains=domains)
            session.rollback()
        else:
            payload = run_backfill(session, limit=args.limit,
                                   budget_seconds=args.budget_seconds,
                                   domains=domains).as_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
