-- Отдельные БД и роли (ТЗ §4.5).
--
-- Разделение не косметическое: §6.5 требует, чтобы health не видел
-- deploy-credentials, а engineering — health. Общий суперпользователь на все
-- сервисы сделал бы это разделение декларацией.

\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS vector;

-- LiteLLM
CREATE ROLE litellm LOGIN;
CREATE DATABASE litellm OWNER litellm;

-- n8n (Milestone B)
CREATE ROLE n8n LOGIN;
CREATE DATABASE n8n OWNER n8n;

-- Forgejo (Milestone B)
CREATE ROLE forgejo LOGIN;
CREATE DATABASE forgejo OWNER forgejo;

\connect helm

-- Health-схема с ограниченными правами (§4.5, §6.5, ADR-005).
CREATE SCHEMA IF NOT EXISTS health;
CREATE ROLE helm_health LOGIN;
GRANT USAGE ON SCHEMA health TO helm_health;
ALTER DEFAULT PRIVILEGES IN SCHEMA health
  GRANT SELECT, INSERT ON TABLES TO helm_health;
-- Health-роль не видит ничего за пределами своей схемы: §30.9
-- «cross-domain RAG access» проверяется именно этим.
REVOKE ALL ON SCHEMA public FROM helm_health;

-- Роль приложения: append-only журнал (§7.2).
--
-- CREATE, а не только USAGE: в этой системе нет отдельной "миграционной"
-- роли — Alembic (migrations/env.py) подключается тем же HELM_DATABASE_URL,
-- что и runtime API, то есть от имени helm_app. Найдено на реальном P2
-- bring-up: без CREATE `alembic upgrade head` падал InsufficientPrivilege
-- прямо на "CREATE TABLE alembic_version". Разница между «может создавать
-- таблицы» и «не может» для единственной runtime-роли не даёт реальной
-- защиты сама по себе — SQL injection предотвращается на уровне кода
-- (параметризованные запросы SQLAlchemy), а не гранулярностью прав здесь
-- (§2 простота: не вводим вторую роль ради защиты, которую и так даёт
-- уровень выше).
--
-- v3.8: Knowledge стал multi-tenant (§14.2), и helm_app владеет теми же
-- tenant-scoped таблицами, что и до этого — владелец таблицы по умолчанию
-- обходит RLS независимо от CREATE/USAGE. Изоляция между
-- knowledge_user_id держится не отзывом CREATE у этой роли (это не
-- защитило бы вообще ничего), а `ALTER TABLE ... FORCE ROW LEVEL
-- SECURITY` в миграции (`helm_core/knowledge/rls.py`) — она форсирует
-- политики даже для владельца, оставаясь единственной runtime-ролью.
CREATE ROLE helm_app LOGIN;
GRANT USAGE, CREATE ON SCHEMA public TO helm_app;
