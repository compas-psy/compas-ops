"""v3.8 §14.4 "PostgreSQL defense in depth" — DDL для RLS на tenant-
scoped Knowledge-таблицах.

Единственный источник списка таблиц/политики: используется и
Alembic-миграцией (`migrations/versions/..._knowledge_rls.py`), и
тестовой фикстурой (`tests/conftest.py`) — `Base.metadata.create_all()`
не создаёт `CREATE POLICY`/`FORCE ROW LEVEL SECURITY` (это не часть
SQLAlchemy ORM metadata), так что без явного вызова `apply_rls()` в
тестах pytest проверял бы только ORM-схему, никогда сами политики.

`knowledge_users`/`channel_identities`/`invites`/`user_usage` сюда
намеренно НЕ входят — см. docstring в файле миграции.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

TENANT_SCOPED_TABLES = (
    "knowledge_sources", "knowledge_chunks", "knowledge_notes", "knowledge_relations",
    "knowledge_ingest_jobs", "knowledge_pending_attachments", "knowledge_ingest_batches",
    "knowledge_batch_items", "knowledge_answer_runs", "knowledge_memories",
)

POLICY_NAME = "knowledge_tenant_isolation"

#: `current_setting(..., true)` — `true` = missing_ok, вернуть NULL, не
#: бросить ошибку, если GUC ещё не выставлен этой транзакцией. НО: на
#: пуле соединений (SQLAlchemy/production и просто повторное
#: использование физического подключения между тестами) once custom GUC
#: тронут `SET LOCAL` хотя бы раз за время жизни сессии — Postgres
#: заводит для него placeholder с default `''` (пустая строка), НЕ NULL;
#: следующая транзакция на том же соединении, которая не вызвала
#: bind_knowledge_user(), видит `current_setting(...) = ''`, и `''::uuid`
#: — это ОШИБКА кастинга (падение запроса), не NULL (найдено этим же
#: тестовым прогоном: test_webhook_calls_chief_when_probe_finds_nothing
#: на пуле соединений после других тестов). NULLIF(..., '') нормализует
#: обе формы ("не тронуто вовсе" и "тронуто раньше, не тронуто сейчас")
#: к одному NULL — тогда `knowledge_user_id = NULL` даёт NULL (fail
#: closed: ни одной строки), а не падение запроса.
PREDICATE = (
    "knowledge_user_id = NULLIF(current_setting("
    "'app.current_knowledge_user_id', true), '')::uuid"
)


def apply_rls(connection: Connection) -> None:
    """`FORCE`, не только `ENABLE` — таблицами владеет та же роль
    (`helm_app`/тестовая `helm`), что накатывает миграции и обслуживает
    рантайм; без FORCE Postgres не применяет RLS к владельцу таблицы
    (см. V3.8-DELTA.md, "RLS не подействует без FORCE")."""
    for table in TENANT_SCOPED_TABLES:
        connection.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        connection.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        connection.execute(text(
            f"CREATE POLICY {POLICY_NAME} ON {table} "
            f"USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
        ))
