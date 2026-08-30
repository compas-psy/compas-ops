"""v3.8 §14.4, P8.6.4 — per-user квоты/backpressure.

"One user must not starve others or kill 12 GB VPS" — единственная
runtime-опасность, которую этот модуль закрывает: storage/daily-ingest
байтовые квоты и предел глубины очереди на пользователя. Fair
round-robin между тенантами — `worker.py::claim_next_job()`, не здесь.

Осознанно упрощено (см. `V3.8-DELTA.md`): `KnowledgeUserUsage.
storage_bytes` монотонно растёт — отключение/архивирование source
(`disable_created_sources`) квоту не освобождает. Полный учёт
"свободного места после удаления" — отдельная, не начатая задача;
здесь достаточно "не дать разово залить сервер сверх лимита", не
точный биллинг.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .tenancy import bind_knowledge_user
from ..models import KnowledgeIngestJob, KnowledgeIngestStatus, KnowledgeUser, KnowledgeUserUsage
from ..models.base import utcnow

#: §14.4 "queue quota" — спека оставляет число открытым ("start 1" для
#: тяжёлых воркеров, per-user config не задан явно числом). Глобальная
#: константа, не колонка `KnowledgeUser` — начинаем с простого явного
#: предела, не вводим ещё одно квотируемое поле в схему, пока не появился
#: конкретный повод сделать его настраиваемым per-user.
MAX_QUEUED_JOBS_PER_USER = 50


class QuotaExceeded(Exception):
    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


def _get_or_create_usage(session: Session, knowledge_user_id: uuid.UUID) -> KnowledgeUserUsage:
    usage = session.get(KnowledgeUserUsage, knowledge_user_id)
    if usage is None:
        usage = KnowledgeUserUsage(knowledge_user_id=knowledge_user_id)
        session.add(usage)
        session.flush()
    return usage


def check_and_record_ingest(session: Session, *, knowledge_user_id: uuid.UUID | None,
                            size_bytes: int) -> None:
    """Поднимает `QuotaExceeded` ДО записи — вызывающая сторона обязана
    проверить это РАНЬШЕ самой записи файла (§14.4 "oversized user
    upload rejected before resource exhaustion"), не постфактум.
    Успешный вызов сразу увеличивает счётчики — проверка и запись
    неразделимы, иначе конкурентные загрузки могли бы обе пройти
    проверку до того, как любая из них учтётся.

    `knowledge_user_id=None` — тот же принцип, что везде в Knowledge:
    разрешается в SYSTEM_OWNER, у которого квоты сегодня не заданы
    (`storage_quota_bytes`/`daily_ingest_quota_bytes` — `None` = без
    ограничения, backfill v3.8 не проставлял никаких лимитов владельцу).
    """
    knowledge_user_id = bind_knowledge_user(session, knowledge_user_id)
    user = session.get(KnowledgeUser, knowledge_user_id)
    usage = _get_or_create_usage(session, knowledge_user_id)
    now = utcnow()
    # §14.4: дневной счётчик — ленивый сброс "по факту", не отдельная
    # cron-задача на полночь: если с последнего обновления наступил новый
    # календарный день (UTC), счётчик этого дня трактуется как 0 ДО
    # прибавления новых байт.
    if usage.updated_at is None or usage.updated_at.date() != now.date():
        usage.ingest_bytes_today = 0

    if (user.storage_quota_bytes is not None
            and usage.storage_bytes + size_bytes > user.storage_quota_bytes):
        raise QuotaExceeded(
            "storage", f"квота хранилища исчерпана (лимит {user.storage_quota_bytes} байт)")
    if (user.daily_ingest_quota_bytes is not None
            and usage.ingest_bytes_today + size_bytes > user.daily_ingest_quota_bytes):
        raise QuotaExceeded(
            "daily_ingest",
            f"дневная квота загрузки исчерпана (лимит {user.daily_ingest_quota_bytes} байт/сутки)")

    usage.storage_bytes += size_bytes
    usage.ingest_bytes_today += size_bytes
    usage.updated_at = now
    session.flush()


def check_queue_depth(session: Session, *, knowledge_user_id: uuid.UUID | None) -> None:
    """§14.4 "queue quota" — проверяется ДО постановки новой работы (при
    получении файла/архива), не после: чтобы не создавать сотни
    `KnowledgeIngestJob`, из которых половина потом откатывается."""
    knowledge_user_id = bind_knowledge_user(session, knowledge_user_id)
    # Explicit-предикат в коде — первый слой (§14.4 "PostgreSQL defense
    # in depth"), RLS выше — второй; ни один не заменяет другой, даже
    # если формально RLS сам по себе тут уже отфильтровал бы строки.
    count = session.scalar(
        select(func.count()).select_from(KnowledgeIngestJob).where(
            KnowledgeIngestJob.knowledge_user_id == knowledge_user_id,
            KnowledgeIngestJob.status.in_(
                (KnowledgeIngestStatus.PENDING, KnowledgeIngestStatus.RUNNING)),
        )
    )
    if count is not None and count >= MAX_QUEUED_JOBS_PER_USER:
        raise QuotaExceeded(
            "queue_depth", f"слишком много задач в очереди (лимит {MAX_QUEUED_JOBS_PER_USER})")
