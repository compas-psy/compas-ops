#!/bin/bash
# Достраивает health-изоляцию (§4.5, §6.5, ADR-005) — schema и роль
# helm_health уже заведены compose/init/01-databases.sql при первом
# старте кластера, но БЕЗ пароля и БЕЗ таблиц: этот скрипт завершает
# бутстрап на уже работающем сервере, идемпотентно.
#
# Пароль генерируется НА СЕРВЕРЕ, ни разу не проходя через агента ни в
# каком виде (лог, stdout, git) — тот же приём, что уже применён к
# restic_password (scripts/setup_backup.sh). Секрет создаёт этот скрипт,
# не человек руками, но значение никогда не покидает сервер и никогда
# не попадает в переписку — CLAUDE.md §5.4 требует именно это, не
# буквально "печатает человек", а "не проходит через агента/чат".
#
# ПОРЯДОК ДЕПЛОЯ (docker-compose.yml уже объявляет health_database_url
# как required secret у helm-core/helm-knowledge-worker):
#   1. ДО раскатки этого compose-файла: `sudo touch /etc/helm/secrets/
#      health_database_url && sudo chown root:helm-secrets ... && sudo
#      chmod 640 ...` — пустой файл-плейсхолдер, иначе `docker compose up`
#      падает на попытке смонтировать несуществующий file-secret.
#      Пустое содержимое = health-путь выключен (fail-open), как и
#      задумано, пока этот скрипт не прогнан.
#   2. Обычный деплой (образ + секции secrets: compose) — health по-
#      прежнему выключен, ничего не сломано.
#   3. Прогнать этот скрипт — заполняет секрет реальным паролем, создаёт
#      health.* таблицы.
#   4. `docker compose restart helm-core helm-knowledge-worker` —
#      HELM_HEALTH_DATABASE_URL_FILE читается один раз при старте
#      процесса (helm_core/config.py::_resolve_file_env_vars), обновление
#      файла на диске само по себе контейнер не подхватывает.
set -euo pipefail

SECRETS_DIR=/etc/helm/secrets
SECRET_FILE="$SECRETS_DIR/health_database_url"

# НАЙДЕНО 01.09.2026: search_path=health (без public) ломает pgvector —
# сам тип `vector` и оператор `<=>` определены в public (см. коммент у
# HealthKnowledgeChunk.embedding), а `unknown`-параметр эмбеддинга
# резолвится в него ТОЛЬКО если public виден в search_path текущего
# соединения. Без этого: "operator does not exist: public.vector <=>
# unknown" — health-часть векторного поиска падает целиком на любом
# запросе, где лексика не набрала полный колчан сама.
#
# Пароль генерируется один раз (идемпотентно) и НИКОГДА не проходит
# через агента — но DSN-строку (в т.ч. этот search_path) нужно уметь
# чинить и на уже существующем секрете, поэтому пароль читается из
# файла, а не только генерируется заново.
if [ -s "$SECRET_FILE" ]; then
  echo "health_database_url уже существует — пароль не трогаем, читаю его из файла для перезаписи DSN"
  PGPASS=$(sudo sed -n 's#.*://helm_health:\([^@]*\)@.*#\1#p' "$SECRET_FILE")
else
  echo "== генерирую пароль helm_health на сервере =="
  # openssl rand -hex — только [0-9a-f], гарантированно без кавычек и
  # SQL-спецсимволов: безопасно подставлять напрямую в heredoc без
  # экранирования (не общий случай, а следствие гарантированного
  # алфавита конкретно этой генерации).
  PGPASS=$(openssl rand -hex 32)
  sudo docker exec -i helm-postgres-1 psql -U helm -d helm -v ON_ERROR_STOP=1 \
    <<SQL >/dev/null
ALTER ROLE helm_health LOGIN PASSWORD '$PGPASS';
SQL
fi
printf 'postgresql+psycopg://helm_health:%s@postgres/helm?options=-csearch_path%%3Dhealth%%2Cpublic' "$PGPASS" \
  | sudo tee "$SECRET_FILE" >/dev/null
sudo chown root:helm-secrets "$SECRET_FILE"
sudo chmod 640 "$SECRET_FILE"
unset PGPASS
echo "health_database_url записан (значение нигде не печаталось)"

# Миграция public не нужна вообще: original_filename в public.
# knowledge_sources и так nullable, raw_path/mime_type/parser для health
# остаются в public как у любого другого домена (не идентифицируют
# документ, см. HealthKnowledgeSourcePrivate). helm_health НЕ получает
# вообще никаких прав на public (ни SELECT, ни REFERENCES) — source_id в
# sidecar ниже без FK именно поэтому.

echo "== создаю схема-объекты health.* (идемпотентно) =="
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -v ON_ERROR_STOP=1 <<'SQL'
-- helm_health получил только USAGE в 01-databases.sql — CREATE не был
-- выдан изначально, таблицы некому было создать.
GRANT CREATE ON SCHEMA health TO helm_health;

-- Владелец — helm_health, не helm_app: в PostgreSQL владелец объекта
-- обходит любой REVOKE на сам объект (REVOKE ограничивает НЕ-владельцев),
-- поэтому "helm_app не видит health" работает только если helm_app
-- никогда не является владельцем этих таблиц. helm — суперпользователь,
-- создаёт от имени helm_health через SET ROLE, а не от своего.
SET ROLE helm_health;

-- Generic public envelope + security-scope private payload (решение
-- владельца при разборе P12): sidecar с чувствительными полями одного
-- health-source, НЕ полное зеркало knowledge_sources. Единый конверт
-- (public.knowledge_sources) + единая очередь (public.knowledge_
-- ingest_jobs) остаются общими для всех доменов — не дублируем
-- fair-queue/retry-логику ради одного домена.
CREATE TABLE IF NOT EXISTS health.knowledge_source_private (
  -- Без REFERENCES public.knowledge_sources(id) намеренно: конверт и
  -- sidecar пишутся в двух разных транзакциях на двух разных
  -- соединениях (helm_app/helm_health) — FK через границу схем
  -- потребовал бы, чтобы обе строки были видны друг другу при вставке,
  -- а helm_health вообще не должен иметь прав на public. Целостность —
  -- на стороне кода (helm_core/knowledge/health_schema.py).
  --
  -- Только original_filename здесь — raw_path/mime_type/parser не
  -- идентифицируют документ, остаются в public.knowledge_sources как у
  -- любого другого домена (см. tables.py::KnowledgeSource.raw_path).
  source_id uuid PRIMARY KEY,
  knowledge_user_id uuid,
  original_filename varchar(255),
  parse_error text,
  created_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_source_private_user
  ON health.knowledge_source_private (knowledge_user_id);

CREATE TABLE IF NOT EXISTS health.knowledge_chunks (
  id uuid PRIMARY KEY,
  knowledge_user_id uuid,
  source_id uuid NOT NULL REFERENCES health.knowledge_source_private(source_id),
  ordinal integer NOT NULL,
  text text NOT NULL,
  tsv tsvector,
  -- Тип vector живёт в public (туда встала расширение pgvector,
  -- compose/init/01-databases.sql) — схема-квалификация нужна, потому
  -- что search_path сессии, создающей таблицу, не гарантированно
  -- включает public при SET ROLE.
  embedding public.vector(384),
  created_at timestamptz NOT NULL,
  CONSTRAINT uq_health_knowledge_chunks_source_ordinal UNIQUE (source_id, ordinal)
);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_chunks_tsv
  ON health.knowledge_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_chunks_user
  ON health.knowledge_chunks (knowledge_user_id);

-- Зеркало public.knowledge_relations (ADR-005/P12): to_id/from_id могут
-- прямо называть тему заметки ("аутоиммунный гастрит") — health entities/
-- topics, которым решение владельца запрещает попадать в public.
CREATE TABLE IF NOT EXISTS health.knowledge_relations (
  id uuid PRIMARY KEY,
  knowledge_user_id uuid,
  from_id varchar(128) NOT NULL,
  to_id varchar(128) NOT NULL,
  relation_type varchar(32) NOT NULL,
  evidence_type varchar(16) NOT NULL,
  source_id uuid REFERENCES health.knowledge_source_private(source_id),
  confidence numeric(4,3),
  created_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_relations_from
  ON health.knowledge_relations (from_id);

-- ADR-019: L2 semantic-atomizer заметки для health — тот же принцип, что
-- knowledge_relations выше: slug/type могут прямо называть тему заметки
-- ("аутоиммунный гастрит"), не более "особый" случай, чем relations,
-- просто ещё одна таблица под той же маршрутизацией (atomizer.py не
-- знает про health вообще, ветвится на is_health_domain()/
-- health_schema_configured(), как и все остальные call site'ы).
CREATE TABLE IF NOT EXISTS health.knowledge_notes (
  id uuid PRIMARY KEY,
  knowledge_user_id uuid,
  slug varchar(128) NOT NULL,
  type varchar(32) NOT NULL,
  domain varchar(32) NOT NULL,
  file_path text NOT NULL,
  source_ids jsonb,
  source_sha256 jsonb,
  sensitivity varchar(32) NOT NULL DEFAULT 'internal',
  trust varchar(32) NOT NULL DEFAULT 'extracted',
  confidence numeric(4,3),
  status varchar(16) NOT NULL DEFAULT 'active',
  supersedes jsonb,
  contradicts jsonb,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  CONSTRAINT uq_health_knowledge_notes_user_slug UNIQUE (knowledge_user_id, slug)
);

RESET ROLE;
SQL

echo "== RLS на health.* (тот же предикат, что ADR-030) =="
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -v ON_ERROR_STOP=1 <<'SQL'
ALTER TABLE health.knowledge_source_private ENABLE ROW LEVEL SECURITY;
ALTER TABLE health.knowledge_source_private FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS knowledge_tenant_isolation ON health.knowledge_source_private;
CREATE POLICY knowledge_tenant_isolation ON health.knowledge_source_private
  USING (knowledge_user_id = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid)
  WITH CHECK (knowledge_user_id = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid);

ALTER TABLE health.knowledge_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE health.knowledge_chunks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS knowledge_tenant_isolation ON health.knowledge_chunks;
CREATE POLICY knowledge_tenant_isolation ON health.knowledge_chunks
  USING (knowledge_user_id = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid)
  WITH CHECK (knowledge_user_id = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid);

ALTER TABLE health.knowledge_relations ENABLE ROW LEVEL SECURITY;
ALTER TABLE health.knowledge_relations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS knowledge_tenant_isolation ON health.knowledge_relations;
CREATE POLICY knowledge_tenant_isolation ON health.knowledge_relations
  USING (knowledge_user_id = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid)
  WITH CHECK (knowledge_user_id = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid);

ALTER TABLE health.knowledge_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE health.knowledge_notes FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS knowledge_tenant_isolation ON health.knowledge_notes;
CREATE POLICY knowledge_tenant_isolation ON health.knowledge_notes
  USING (knowledge_user_id = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid)
  WITH CHECK (knowledge_user_id = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid);
SQL

echo "== проверка: helm_health не видит public, не суперпользователь =="
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
  "select rolname, rolsuper, rolbypassrls from pg_roles where rolname = 'helm_health'"
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
  "select has_schema_privilege('helm_health', 'public', 'USAGE')"
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
  "select tablename, tableowner from pg_tables where schemaname = 'health'"

echo "== проверка: helm_app НЕ может читать health.* (ожидается ошибка/false) =="
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
  "select has_schema_privilege('helm_app', 'health', 'USAGE')"
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
  "select has_table_privilege('helm_app', 'health.knowledge_source_private', 'SELECT')"
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
  "select has_table_privilege('helm_app', 'health.knowledge_chunks', 'SELECT')"
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
  "select has_table_privilege('helm_app', 'health.knowledge_relations', 'SELECT')"
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
  "select has_table_privilege('helm_app', 'health.knowledge_notes', 'SELECT')"

echo "SETUP DONE"
