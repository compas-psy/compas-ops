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
CREATE ROLE helm_app LOGIN;
GRANT USAGE ON SCHEMA public TO helm_app;
