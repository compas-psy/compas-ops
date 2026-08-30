"""v3.8 §14.2 — минимальный tenancy-хелпер.

Единственная точка, которая знает, что «текущий пользователь» почти
везде в кодовой базе сегодня — единственный SYSTEM_OWNER (Dedicated
Knowledge Bot, P8.6.2, ещё не существует, см. V3.8-DELTA.md). Существующие
call sites `ingest.py`/`batch_intake.py` не передают `knowledge_user_id`
явно — они разрешают его через эту функцию.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import KnowledgeUser, KnowledgeUserRole


def resolve_system_owner_id(session: Session) -> uuid.UUID:
    """Вернуть id единственной строки `role=SYSTEM_OWNER`.

    Ровно одна такая строка создаётся backfill-миграцией v3.8. Пока она
    не накатана (или в тестовой БД её не завела фикстура) — явная
    ошибка, не молчаливое создание второй.
    """
    owner_id = session.scalar(
        select(KnowledgeUser.id).where(KnowledgeUser.role == KnowledgeUserRole.SYSTEM_OWNER)
    )
    if owner_id is None:
        raise RuntimeError(
            "no SYSTEM_OWNER KnowledgeUser row — run the v3.8 backfill migration first"
        )
    return owner_id
