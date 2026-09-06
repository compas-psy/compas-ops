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

ВОССТАНОВЛЕНИЕ ПОСЛЕ ПАДЕНИЯ ВОРКЕРА. Добавлено 06.09.2026 по аудиту
владельца. Прежняя схема брала только `PENDING`, а `RUNNING` фиксировался
коммитом ДО разбора: воркер, убитый между этим коммитом и концом
разбора, оставлял задание в `RUNNING` навсегда — ни один следующий
воркер его не видел, и вернуть работу можно было только руками.

Механизм — аренда. Взявший задание владеет им до `lease_expires_at`;
после этого срока задание снова претендуемо. Три свойства, каждое
против своего отказа:

  срок владения не даёт двум воркерам разбирать одно задание, пока
  первый жив и в сроке;
  возврат просроченного возвращает работу после падения без человека;
  `MAX_ATTEMPTS` не даёт заданию, которое роняет воркер каждый раз,
  крутиться вечно: после исчерпания оно становится `FAILED` и видно.

ЧЕГО ЗДЕСЬ НЕТ НАМЕРЕННО. Ни приоритетов, ни расписаний, ни продления
аренды на лету. Продление (heartbeat) нужно, только если разбор
переживает срок аренды; срок взят с большим запасом к наблюдаемому
времени разбора, и пока запас держится, heartbeat — лишняя машинерия.
Если разбор начнёт выходить за срок, это будет видно по повторным
попыткам одного и того же задания, и тогда heartbeat станет обоснован
замером, а не предположением.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import KnowledgeSemanticJob, KnowledgeSemanticRun, KnowledgeSource
from ..models.base import KnowledgeIngestStatus, SemanticRunStatus
from .semantic_publish import SEMANTIC_VERSION, publish_semantic_run
from .tenancy import bind_knowledge_user

logger = logging.getLogger(__name__)

#: Сколько задание принадлежит взявшему его воркеру. Наблюдаемый разбор
#: одного источника — минуты (приёмка P2: сорок секунд от файла до
#: знания на среднем документе). Тридцать минут — запас на порядок, а не
#: подгонка: аренда короче времени разбора привела бы к тому, что два
#: воркера разбирают один источник, и это хуже, чем поздний возврат
#: упавшего задания.
LEASE_SECONDS = 30 * 60

#: Сколько раз задание может быть взято, прежде чем будет признано
#: неисполнимым. Три: одна нормальная попытка и два возврата после
#: падения. Больше — это уже не «воркер упал», а «задание роняет
#: воркер», и крутить его молча нельзя.
MAX_ATTEMPTS = 3


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


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


def fail_exhausted_semantic_jobs(session: Session) -> int:
    """Закрыть задания, исчерпавшие попытки, вместо вечного возврата.

    Отдельным проходом, а не внутри `claim`: задание, которое роняет
    воркер каждый раз, иначе молча крутилось бы в очереди и выглядело бы
    как «работа идёт». `FAILED` с явной причиной видно и в журнале, и в
    любом отчёте по очереди.
    """
    stale = session.scalars(
        select(KnowledgeSemanticJob)
        .where(KnowledgeSemanticJob.status == KnowledgeIngestStatus.RUNNING,
               KnowledgeSemanticJob.attempts >= MAX_ATTEMPTS,
               KnowledgeSemanticJob.lease_expires_at.is_not(None),
               KnowledgeSemanticJob.lease_expires_at < _now())
        .with_for_update(skip_locked=True)).all()
    for job in stale:
        job.status = KnowledgeIngestStatus.FAILED
        job.error = "LeaseExpiredAfterMaxAttempts"
        logger.warning("semantic job %s исчерпал попытки (%s) и закрыт",
                       job.id, job.attempts)
    if stale:
        session.flush()
    return len(stale)


def claim_next_semantic_job(session: Session) -> KnowledgeSemanticJob | None:
    """Взять одно задание: новое либо брошенное упавшим воркером.

    `FOR UPDATE SKIP LOCKED` защищает от одновременного взятия двумя
    воркерами: строка заблокирована на время самого взятия, второй
    воркер её просто не видит и берёт следующую. От разбора одного
    задания дважды защищает уже аренда — блокировку на всё время разбора
    держать нельзя, он идёт минутами.

    Претендуемо задание либо новое (`PENDING`), либо `RUNNING` с
    истёкшей арендой — то есть брошенное. Второй случай и есть
    восстановление: до 06.09.2026 такое задание не видел никто.
    """
    now = _now()
    job = session.scalar(
        select(KnowledgeSemanticJob)
        .where(
            KnowledgeSemanticJob.attempts < MAX_ATTEMPTS,
            or_(
                KnowledgeSemanticJob.status == KnowledgeIngestStatus.PENDING,
                and_(KnowledgeSemanticJob.status == KnowledgeIngestStatus.RUNNING,
                     or_(KnowledgeSemanticJob.lease_expires_at.is_(None),
                         KnowledgeSemanticJob.lease_expires_at < now)),
            ),
        )
        .order_by(KnowledgeSemanticJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True))
    if job is None:
        return None
    if job.status == KnowledgeIngestStatus.RUNNING:
        # Возврат брошенного. Ревизия, начатая упавшим воркером, осталась
        # в RUNNING и текущей стать уже не может (§14.20 — только READY):
        # она закрывается здесь, иначе зомби копятся с каждой попыткой и
        # отчёт по ревизиям перестаёт быть читаемым.
        _abandon_orphan_run(session, job)
        logger.warning("semantic job %s возвращён в работу: аренда истекла, попытка %s",
                       job.id, job.attempts + 1)
    job.status = KnowledgeIngestStatus.RUNNING
    job.attempts += 1
    job.lease_expires_at = now + dt.timedelta(seconds=LEASE_SECONDS)
    session.flush()
    return job


def _abandon_orphan_run(session: Session, job: KnowledgeSemanticJob) -> None:
    """Пометить незавершённую ревизию прошлой попытки как провалённую."""
    orphans = session.scalars(
        select(KnowledgeSemanticRun)
        .where(KnowledgeSemanticRun.source_id == job.source_id,
               KnowledgeSemanticRun.semantic_version == job.semantic_version,
               KnowledgeSemanticRun.status == SemanticRunStatus.RUNNING)).all()
    for run in orphans:
        run.status = SemanticRunStatus.FAILED
        logger.warning("ревизия %s брошена упавшим воркером, помечена FAILED", run.id)


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
        job.lease_expires_at = None
        return

    from .semantic_pilot import source_text  # локально: цикл импортов

    text = source_text(source)
    if text is None:
        job.status = KnowledgeIngestStatus.FAILED
        job.error = "NoText"
        job.lease_expires_at = None
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
        job.lease_expires_at = None
        logger.warning("semantic job %s упал: %s", job.id, job.error)
        return

    job.semantic_run_id = result.run_id
    job.status = KnowledgeIngestStatus.DONE
    # Аренда снимается: завершённое задание не претендуемо и без этого
    # (claim берёт только PENDING и RUNNING), но оставленный срок читался
    # бы как «кто-то всё ещё работает».
    job.lease_expires_at = None
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
