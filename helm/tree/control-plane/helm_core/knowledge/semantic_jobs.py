"""Очередь семантического разбора: L1 ставит задание, воркер его берёт.

Заведено 06.09.2026, после того как аудит владельца показал разрыв,
которого никто не видел месяц: обычная загрузка файла до L2 не доходила
ВООБЩЕ. `worker.py::process_job` звал `atomize_and_store()`, а тот с R2
заморожен и возвращает 0 (`atomizer.py:411`); настоящий
`publish_semantic_run()` вызывали только три ручных CLI. Корпус из 90
источников существовал ровно потому, что backfill запускали руками, а
любой новый документ оставался L1-чанками без единого узла.

ПОЧЕМУ ОЧЕРЕДЬ, А НЕ ВЫЗОВ НА МЕСТЕ. Разбор идёт минутами и зовёт
локальную модель. Делать его внутри той же транзакции, что парсинг и
чанки, значит держать открытой транзакцию всё это время и терять
результат парсинга при любом сбое модели. Отдельное задание даёт три
вещи, которых иначе нет: продолжение после перезапуска воркера,
раздельные состояния L1 и L2, и одну точку, из которой видно, сколько
работы стоит в очереди.

ЧЕГО ЗДЕСЬ НЕТ НАМЕРЕННО. Никаких приоритетов, расписаний и повторов по
таймеру: пока не измерено, что они нужны, это лишние ручки. Счётчик
попыток есть, автоповтора — нет; упавшее задание видно и разбирается,
а не крутится молча.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import KnowledgeSemanticJob, KnowledgeSource
from ..models.base import KnowledgeIngestStatus, SemanticRunStatus
from .semantic_publish import SEMANTIC_VERSION, publish_semantic_run
from .tenancy import bind_knowledge_user

logger = logging.getLogger(__name__)


def enqueue_semantic(session: Session, *, source_id: uuid.UUID,
                     knowledge_user_id: uuid.UUID, source_sha256: str,
                     semantic_version: int = SEMANTIC_VERSION) -> uuid.UUID | None:
    """Поставить разбор источника в очередь. Повтор — не ошибка.

    Ключ работы — четвёрка «пользователь, источник, содержимое, версия».
    Загрузка тех же байтов второй раз попадает в тот же ключ и нового
    задания не создаёт: это то самое свойство, из-за которого дубликат
    файла не превращается во второе знание о том же документе.

    Возвращает id созданного задания либо `None`, если такое уже есть.
    Вызывается ВНУТРИ транзакции L1 — задание и результат парсинга
    появляются вместе или не появляются вовсе.
    """
    stmt = (pg_insert(KnowledgeSemanticJob)
            .values(id=uuid.uuid4(), knowledge_user_id=knowledge_user_id,
                    source_id=source_id, source_sha256=source_sha256,
                    semantic_version=semantic_version,
                    status=KnowledgeIngestStatus.PENDING)
            .on_conflict_do_nothing(constraint="uq_knowledge_semantic_jobs_work")
            .returning(KnowledgeSemanticJob.id))
    return session.execute(stmt).scalar_one_or_none()


def claim_next_semantic_job(session: Session) -> KnowledgeSemanticJob | None:
    """Взять одно задание. `FOR UPDATE SKIP LOCKED`, как у очереди L1.

    Блокировка снимается сразу переводом в RUNNING и коммитом
    вызывающего: держать её на всё время разбора нельзя, он идёт
    минутами.
    """
    job = session.scalar(
        select(KnowledgeSemanticJob)
        .where(KnowledgeSemanticJob.status == KnowledgeIngestStatus.PENDING)
        .order_by(KnowledgeSemanticJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True))
    if job is None:
        return None
    job.status = KnowledgeIngestStatus.RUNNING
    job.attempts += 1
    session.flush()
    return job


def process_semantic_job(session: Session, job: KnowledgeSemanticJob) -> None:
    """Разобрать источник и опубликовать ревизию.

    Задание отвечает на вопрос «работа выполнялась», ревизия — на вопрос
    «что получилось»: `DEGRADED`-ревизия это выполненная работа с плохим
    результатом, а не невыполненная. Поэтому `DONE` ставится и на неё, а
    `FAILED` — только когда разбор упал и ревизии нет.
    """
    bind_knowledge_user(session, job.knowledge_user_id)
    source = session.get(KnowledgeSource, job.source_id)
    if source is None:
        job.status = KnowledgeIngestStatus.FAILED
        job.error = "SourceMissing"
        return

    from .semantic_pilot import source_text  # локально: цикл импортов

    text = source_text(source)
    if text is None:
        job.status = KnowledgeIngestStatus.FAILED
        job.error = "NoText"
        return

    try:
        result = publish_semantic_run(session, source=source, text=text,
                                      semantic_version=job.semantic_version)
    except Exception as exc:
        session.rollback()
        bind_knowledge_user(session, job.knowledge_user_id)
        job = session.get(KnowledgeSemanticJob, job.id)
        job.status = KnowledgeIngestStatus.FAILED
        # Только имя класса: текст ошибки модели может содержать кусок
        # разбираемого документа (та же причина, что в backfill.py).
        job.error = type(exc).__name__
        logger.warning("semantic job %s упал: %s", job.id, job.error)
        return

    job.semantic_run_id = result.run_id
    job.status = KnowledgeIngestStatus.DONE
    logger.info("semantic job %s -> %s (switched=%s)", job.id, result.status, result.switched)

    if result.status == SemanticRunStatus.READY:
        # ПОЧЕМУ ЗДЕСЬ, А НЕ ОТДЕЛЬНЫМ ПРОГОНОМ. 06.09.2026 переключение
        # корпуса на semantic-v3 сменило текущие ревизии у всех 90
        # источников, а слой личностей остался над прежним поколением —
        # и структурный ответ владельцу опустел полностью, при целых
        # данных. Между двумя ручными прогонами память была пуста.
        # Переключение поколения обязано тянуть разрешение сущностей в
        # той же операции.
        from .entity_resolution import resolve_all  # локально: цикл импортов

        resolve_all(session, knowledge_user_id=job.knowledge_user_id)
