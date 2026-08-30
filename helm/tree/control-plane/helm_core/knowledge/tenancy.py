"""v3.8 §14.2/§14.4 — минимальный tenancy-хелпер + PostgreSQL RLS-биндинг.

Единственная точка, которая знает, что «текущий пользователь» почти
везде в кодовой базе сегодня — единственный SYSTEM_OWNER (Dedicated
Knowledge Bot, P8.6.2, ещё не существует, см. V3.8-DELTA.md). Существующие
call sites `ingest.py`/`chat_intake.py`/`batch_intake.py`/`probe.py`/
`worker.py` не передают `knowledge_user_id` явно — они разрешают его
через `resolve_system_owner_id()`.

`set_current_knowledge_user()`/`bind_knowledge_user()` — вторая половина
defense-in-depth (§14.4 "PostgreSQL defense in depth"): explicit-предикат
в коде (уже есть на каждом query) + RLS-политики (миграция
`ef1ba5467e14`/следующая RLS-миграция), которые физически не могут
отдать чужую строку, даже если предикат в коде однажды забудут дописать.
Без вызова `bind_knowledge_user()` перед первым обращением к tenant-
scoped Knowledge-таблице в транзакции политики видят
`current_setting(..., true)` как NULL и, при `FORCE ROW LEVEL SECURITY`,
не отдают НИ ОДНОЙ строки — так и задумано (fail closed), но именно
поэтому вызов обязателен на каждом входе, а не опционален.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
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


def set_current_knowledge_user(session: Session, knowledge_user_id: uuid.UUID) -> None:
    """`SET LOCAL app.current_knowledge_user_id` — держит RLS-политики
    (`current_setting('app.current_knowledge_user_id', true)::uuid`).

    `set_config(..., true)` — третий аргумент `is_local=true`, тот же
    эффект, что `SET LOCAL`, но параметризуемо: сам `SET LOCAL x = :v`
    не принимает bind-параметры (ограничение протокола Postgres), а
    `set_config()` — обычная функция, значение остаётся bind-параметром,
    не строковой склейкой UUID в SQL. Держится до конца ТЕКУЩЕЙ
    транзакции — вызывать заново после commit/rollback в той же сессии.
    """
    session.execute(
        text("SELECT set_config('app.current_knowledge_user_id', :id, true)"),
        {"id": str(knowledge_user_id)},
    )


def bind_knowledge_user(session: Session, knowledge_user_id: uuid.UUID | None) -> uuid.UUID:
    """Разрешить тенанта (SYSTEM_OWNER по умолчанию) И сразу привязать
    RLS-сессию к нему — единая точка входа для каждой функции, которая
    трогает tenant-scoped Knowledge-таблицу, чтобы предикат в коде и GUC
    для RLS никогда не разошлись (одно за другим, не два отдельных шага,
    которые можно забыть рассинхронизировать).
    """
    if knowledge_user_id is None:
        knowledge_user_id = resolve_system_owner_id(session)
    set_current_knowledge_user(session, knowledge_user_id)
    return knowledge_user_id
