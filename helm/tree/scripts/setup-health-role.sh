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

-- semantic-v2 (v4.0 §14.5, "private equivalents/adapters under health
-- schema"). Зеркалятся четыре таблицы из пяти: knowledge_semantic_runs
-- остаётся только в public, в ней нет ни одного поля с содержимым
-- источника — счётчики окон, имя модели, её отпечаток. Тот же довод, по
-- которому конверт public.knowledge_sources един для всех доменов.
--
-- Чувствительное здесь — canonical_label ("визит к гастроэнтерологу"),
-- normalized_key, subtype, alias ("Безручко Д.Ю.") и role у ребра: те
-- же "health entities/topics", ради которых сюда уехали
-- knowledge_relations и knowledge_notes.
--
-- semantic_run_id без REFERENCES: прогон живёт в public, а helm_health
-- не имеет там никаких прав. Та же причина, что у source_id сайдкара.
CREATE TABLE IF NOT EXISTS health.knowledge_nodes (
  id uuid PRIMARY KEY,
  knowledge_user_id uuid NOT NULL,
  kind varchar(16) NOT NULL,
  subtype varchar(64),
  -- R3.1, найдено владельцем 02.09.2026: entity_type (подвид ENTITY) и
  -- statement_text (тело утверждения EVENT/FACT/DECISION/CONCEPT) —
  -- обе колонки терялись на записи, см. tables.py::KnowledgeNode.
  entity_type varchar(64),
  statement_text text,
  canonical_label text NOT NULL,
  normalized_key text,
  primary_domain_id uuid,
  security_scope varchar(32) NOT NULL DEFAULT 'internal',
  occurred_at_start timestamptz,
  occurred_at_end timestamptz,
  date_precision varchar(8),
  valid_from timestamptz,
  valid_to timestamptz,
  status varchar(16) NOT NULL DEFAULT 'active',
  markdown_path text,
  semantic_run_id uuid,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);
-- Сервер, где эта таблица уже создана прошлым прогоном (до R3.1), не
-- получит новых колонок от CREATE TABLE IF NOT EXISTS выше — оно не
-- трогает существующую таблицу. ADD COLUMN IF NOT EXISTS идемпотентен
-- в обе стороны: и на свежей таблице (колонки уже есть — no-op), и на
-- старой (колонки появляются).
ALTER TABLE health.knowledge_nodes ADD COLUMN IF NOT EXISTS entity_type varchar(64);
ALTER TABLE health.knowledge_nodes ADD COLUMN IF NOT EXISTS statement_text text;
CREATE INDEX IF NOT EXISTS ix_health_knowledge_nodes_user_kind
  ON health.knowledge_nodes (knowledge_user_id, kind);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_nodes_resolution
  ON health.knowledge_nodes (knowledge_user_id, kind, subtype, normalized_key);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_nodes_run
  ON health.knowledge_nodes (semantic_run_id);

CREATE TABLE IF NOT EXISTS health.knowledge_node_mentions (
  id uuid PRIMARY KEY,
  knowledge_user_id uuid NOT NULL,
  node_id uuid NOT NULL REFERENCES health.knowledge_nodes(id),
  source_id uuid NOT NULL REFERENCES health.knowledge_source_private(source_id),
  window_id integer,
  chunk_id uuid REFERENCES health.knowledge_chunks(id),
  page integer,
  time_start_ms integer,
  time_end_ms integer,
  char_start integer,
  char_end integer,
  evidence_text_hash varchar(64),
  evidence_type varchar(16) NOT NULL,
  confidence numeric(4,3),
  semantic_run_id uuid,
  created_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_node_mentions_node
  ON health.knowledge_node_mentions (node_id);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_node_mentions_source
  ON health.knowledge_node_mentions (source_id);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_node_mentions_run
  ON health.knowledge_node_mentions (semantic_run_id);

CREATE TABLE IF NOT EXISTS health.knowledge_edges (
  id uuid PRIMARY KEY,
  knowledge_user_id uuid NOT NULL,
  from_node_id uuid NOT NULL REFERENCES health.knowledge_nodes(id),
  to_node_id uuid NOT NULL REFERENCES health.knowledge_nodes(id),
  relation_type varchar(32) NOT NULL,
  role varchar(64),
  source_id uuid REFERENCES health.knowledge_source_private(source_id),
  mention_id uuid REFERENCES health.knowledge_node_mentions(id),
  evidence_node_id uuid REFERENCES health.knowledge_nodes(id),
  evidence_type varchar(16) NOT NULL,
  confidence numeric(4,3),
  status varchar(16) NOT NULL DEFAULT 'active',
  semantic_run_id uuid,
  created_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_edges_from
  ON health.knowledge_edges (from_node_id, relation_type);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_edges_to
  ON health.knowledge_edges (to_node_id, relation_type);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_edges_run
  ON health.knowledge_edges (semantic_run_id);

CREATE TABLE IF NOT EXISTS health.knowledge_entity_aliases (
  id uuid PRIMARY KEY,
  knowledge_user_id uuid NOT NULL,
  entity_node_id uuid NOT NULL REFERENCES health.knowledge_nodes(id),
  alias text NOT NULL,
  normalized_alias text NOT NULL,
  source_id uuid REFERENCES health.knowledge_source_private(source_id),
  confidence numeric(4,3),
  created_at timestamptz NOT NULL,
  CONSTRAINT uq_health_knowledge_entity_aliases_node_alias
    UNIQUE (knowledge_user_id, entity_node_id, normalized_alias)
);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_entity_aliases_lookup
  ON health.knowledge_entity_aliases (knowledge_user_id, normalized_alias);

-- Окна обработки (§14.4.1, R3). Зеркалятся ради heading_path: «Анализы
-- и обследования» → «Биохимический анализ крови» уже медицинская
-- информация. Текст окна не хранится ни здесь, ни в public — он есть в
-- L1 SOURCE и восстанавливается по границам.
CREATE TABLE IF NOT EXISTS health.knowledge_semantic_windows (
  id uuid PRIMARY KEY,
  knowledge_user_id uuid NOT NULL,
  -- Без REFERENCES: прогон живёт в public, прав туда у helm_health нет.
  semantic_run_id uuid NOT NULL,
  source_id uuid NOT NULL REFERENCES health.knowledge_source_private(source_id),
  ordinal integer NOT NULL,
  parent_window_id uuid REFERENCES health.knowledge_semantic_windows(id),
  char_start integer NOT NULL,
  char_end integer NOT NULL,
  heading_path text,
  text_hash varchar(64) NOT NULL,
  status varchar(16) NOT NULL DEFAULT 'pending',
  nodes_created integer NOT NULL DEFAULT 0,
  edges_created integer NOT NULL DEFAULT 0,
  rejected_count integer NOT NULL DEFAULT 0,
  result_hash varchar(64),
  error_code varchar(64),
  created_at timestamptz NOT NULL,
  CONSTRAINT uq_health_knowledge_semantic_windows_run_ordinal
    UNIQUE (semantic_run_id, ordinal)
);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_semantic_windows_run_status
  ON health.knowledge_semantic_windows (semantic_run_id, status);
CREATE INDEX IF NOT EXISTS ix_health_knowledge_semantic_windows_source
  ON health.knowledge_semantic_windows (source_id);

-- Закрытые реестры и цикл ревизии — те же, что в public (R2-hardening,
-- §14.5/§14.9). Отдельным блоком, а не в CREATE TABLE выше: таблицы уже
-- созданы прошлым прогоном без ограничений, а `CREATE TABLE IF NOT
-- EXISTS` к существующей таблице ничего не добавляет. `ADD CONSTRAINT
-- IF NOT EXISTS` в Postgres нет, поэтому проверяем каталог сами.
--
-- Значения выписаны буквально и обязаны совпадать с public: расхождение
-- ловит tests/test_knowledge_semantic_v2_registry.py, сверяющий обе
-- схемы с перечислениями Python.
DO $$
DECLARE
  spec text[][] := ARRAY[
    ['knowledge_nodes', 'ck_knowledge_nodes_kind',
     'kind IN (''entity'', ''event'', ''fact'', ''decision'', ''concept'', ''document_ref'', ''memory_ref'')'],
    ['knowledge_nodes', 'ck_knowledge_nodes_status',
     'status IN (''active'', ''disabled'', ''superseded'', ''quarantine'', ''deleted'')'],
    ['knowledge_nodes', 'ck_knowledge_nodes_date_precision',
     'date_precision IS NULL OR date_precision IN (''day'', ''month'', ''year'', ''unknown'')'],
    ['knowledge_nodes', 'ck_knowledge_nodes_run_required_for_atoms',
     'semantic_run_id IS NOT NULL OR kind IN (''document_ref'', ''entity'', ''memory_ref'')'],
    ['knowledge_node_mentions', 'ck_knowledge_node_mentions_evidence_type',
     'evidence_type IN (''owner_explicit'', ''extracted'', ''inferred'')'],
    ['knowledge_edges', 'ck_knowledge_edges_relation_type',
     'relation_type IN (''involves'', ''has_role'', ''about'', ''located_at'', ''part_of'', ''created_by'', ''owned_by'', ''resulted_in'', ''reason_for'', ''supports'', ''contradicts'', ''supersedes'', ''derived_from'', ''refers_to'', ''related_to'')'],
    ['knowledge_edges', 'ck_knowledge_edges_evidence_type',
     'evidence_type IN (''owner_explicit'', ''extracted'', ''inferred'')'],
    ['knowledge_edges', 'ck_knowledge_edges_status',
     'status IN (''active'', ''disabled'', ''superseded'', ''quarantine'', ''deleted'')'],
    ['knowledge_edges', 'ck_knowledge_edges_run_required_for_derived',
     'semantic_run_id IS NOT NULL OR evidence_type = ''owner_explicit'''],
    ['knowledge_semantic_windows', 'ck_knowledge_semantic_windows_status',
     'status IN (''pending'', ''processed'', ''no_knowledge'', ''split'', ''failed'')'],
    ['knowledge_semantic_windows', 'ck_knowledge_semantic_windows_span_not_empty',
     'char_end > char_start'],
    -- R3.1: те же три инварианта, что в public (tables.py::KnowledgeNode).
    ['knowledge_nodes', 'ck_knowledge_nodes_statement_text_required_for_atoms',
     'kind NOT IN (''event'', ''fact'', ''decision'', ''concept'') OR (statement_text IS NOT NULL AND statement_text <> '''')'],
    ['knowledge_nodes', 'ck_knowledge_nodes_entity_type_required_for_entity',
     'kind <> ''entity'' OR entity_type IS NOT NULL'],
    ['knowledge_nodes', 'ck_knowledge_nodes_statement_text_null_for_entity',
     'kind <> ''entity'' OR statement_text IS NULL']
  ];
  i int;
BEGIN
  FOR i IN 1 .. array_length(spec, 1) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
       WHERE n.nspname = 'health' AND t.relname = spec[i][1] AND c.conname = spec[i][2]
    ) THEN
      EXECUTE format('ALTER TABLE health.%I ADD CONSTRAINT %I CHECK (%s)',
                     spec[i][1], spec[i][2], spec[i][3]);
    END IF;
  END LOOP;
END
$$;

-- Упоминание всегда продукт прохода — исключений нет, поэтому NOT NULL,
-- а не CHECK с оговорками. Таблица пуста, бэкафилл не нужен.
ALTER TABLE health.knowledge_node_mentions ALTER COLUMN semantic_run_id SET NOT NULL;

RESET ROLE;
SQL

echo "== RLS на health.* (тот же предикат, что ADR-030) =="
for table in knowledge_source_private knowledge_chunks knowledge_relations \
             knowledge_notes knowledge_nodes knowledge_node_mentions \
             knowledge_edges knowledge_entity_aliases knowledge_semantic_windows; do
  sudo docker exec -i helm-postgres-1 psql -U helm -d helm -v ON_ERROR_STOP=1 <<SQL
ALTER TABLE health.$table ENABLE ROW LEVEL SECURITY;
ALTER TABLE health.$table FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS knowledge_tenant_isolation ON health.$table;
CREATE POLICY knowledge_tenant_isolation ON health.$table
  USING (knowledge_user_id = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid)
  WITH CHECK (knowledge_user_id = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid);
SQL
done

echo "== проверка: helm_health не видит public, не суперпользователь =="
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
  "select rolname, rolsuper, rolbypassrls from pg_roles where rolname = 'helm_health'"
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
  "select has_schema_privilege('helm_health', 'public', 'USAGE')"
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
  "select tablename, tableowner from pg_tables where schemaname = 'health'"

# Проверка ниже — не отчёт, а условие. Печатать `t` и завершаться словом
# SETUP DONE значит сказать «health-изоляция готова» ровно в том случае,
# когда её нет. Пока таблиц было четыре, это было видно глазами; с
# восемью — уже нет.
echo "== проверка: helm_app НЕ может читать health.* (ожидается false везде) =="
leak=0
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
  "select has_schema_privilege('helm_app', 'health', 'USAGE')"
for table in knowledge_source_private knowledge_chunks knowledge_relations \
             knowledge_notes knowledge_nodes knowledge_node_mentions \
             knowledge_edges knowledge_entity_aliases knowledge_semantic_windows; do
  granted=$(sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
    "select has_table_privilege('helm_app', 'health.$table', 'SELECT')")
  echo "  health.$table: $granted"
  [ "$granted" = "f" ] || leak=$((leak + 1))
done
[ "$leak" -eq 0 ] || { echo "::error::helm_app имеет SELECT на $leak health-таблиц"; exit 1; }

echo "SETUP DONE"
