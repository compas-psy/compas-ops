-- Append-only для task_events (ТЗ §7.2).
--
-- «DB rule запрещает UPDATE/DELETE обычному application role» — буквально:
-- права не выдаются, а не отзываются триггером. Триггер можно отключить
-- сессионной переменной; невыданный GRANT — нельзя.
--
-- Запускается ПОСЛЕ alembic upgrade head, поэтому лежит отдельным шагом
-- в scripts/bootstrap-db.sh, а не в docker-entrypoint-initdb.d.

\set ON_ERROR_STOP on
\connect helm

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO helm_app;

-- И сразу отнимаем изменение истории у роли приложения.
REVOKE UPDATE, DELETE ON task_events FROM helm_app;

ALTER DEFAULT PRIVILEGES FOR ROLE helm IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO helm_app;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO helm_app;
