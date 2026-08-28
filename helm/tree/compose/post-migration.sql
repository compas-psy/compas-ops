-- Append-only для task_events (ТЗ §7.2).
--
-- «DB rule запрещает UPDATE/DELETE обычному application role» — буквально:
-- права не выдаются, а не отзываются триггером. Триггер можно отключить
-- сессионной переменной; невыданный GRANT — нельзя.
--
-- Запускается ПОСЛЕ alembic upgrade head — и именно поэтому лежит здесь,
-- а не в compose/init/: всё из compose/init/ Postgres выполняет
-- автоматически при первом старте контейнера, до каких-либо миграций.
-- Найдено на реальном bring-up: файл раньше лежал в compose/init/ вместе
-- с 01-databases.sql, из-за чего REVOKE на task_events падал с
-- "relation does not exist" ещё до того, как таблица появлялась.
--
-- Запуск вручную после миграций:
--   docker compose -f compose/docker-compose.yml exec -T postgres \
--     psql -U helm -d helm -f - < compose/post-migration.sql

\set ON_ERROR_STOP on
\connect helm

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO helm_app;

-- И сразу отнимаем изменение истории у роли приложения.
REVOKE UPDATE, DELETE ON task_events FROM helm_app;

ALTER DEFAULT PRIVILEGES FOR ROLE helm IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO helm_app;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO helm_app;
