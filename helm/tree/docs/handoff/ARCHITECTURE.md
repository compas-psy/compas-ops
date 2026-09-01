# ARCHITECTURE — обзор системы HELM

Один VPS (`185.250.44.137`, 11GiB RAM, `helm.cmpas.ru`), один
`docker-compose.yml`, один Postgres-кластер. Этот документ — точка
входа для того, кто впервые видит систему целиком; за конкретными
решениями — ADR (`docs/adr/`), за деталями Knowledge — `docs/KNOWLEDGE*.md`.

## Компоненты (docker-compose, 9 сервисов)

| Сервис | Роль | Резидентность |
|---|---|---|
| `postgres` | pgvector/pgvector:pg16 — единственная БД для всего, кроме LiteLLM (у него своя БД в том же кластере) | постоянно |
| `litellm` | Диспетчер платных моделей (ADR-003, `MODEL_ROUTING.md`) | постоянно |
| `helm-core` | Control Plane — FastAPI, canonical state (ADR-001), API для Telegram/MAX/Panel/internal | постоянно |
| `helm-knowledge-worker` | Асинхронный парсинг документов (MarkItDown/Docling/GigaAM), отдельный от `helm-core` контейнер намеренно — тяжёлые зависимости не должны влиять на живой API | постоянно, опрашивает очередь |
| `helm-embed` | Локальный embedding-сервис (MiniLM-L12-v2, ADR-025) | постоянно, healthcheck |
| `ollama` | Z2-рефраз (`gemma2:2b`) | one-shot runtime, `OLLAMA_KEEP_ALIVE=0` |
| `n8n` | Workflow automation | постоянно, `127.0.0.1`-only, коннекторы не заведены (ADR-002, не начато) |
| `forgejo` | Self-hosted git (`git.cmpas.ru`) | постоянно, приватный, целевой primary (ADR-006, миграция не выполнена) |
| `caddy` | Реверс-прокси + статика Panel | постоянно |

Вне docker-compose: **Hermes** — отдельный процесс на хосте
(`~/.hermes/`), не контейнеризован, плагинная архитектура
(`hermes/plugins/helm-control/`). **Guardian** — независимый Python-
скрипт под systemd-таймером, без единой зависимости кроме stdlib
(ADR-011).

## Кто чем владеет (ADR-001)

**Control Plane** — canonical для идентичности, дедупликации входящих
сообщений, состояния задач/approval (Postgres — единственный durable
источник правды об исполненных действиях, ADR-012). **Hermes** —
canonical для разговорного контекста одной сессии (§10.2: полный
transcript живёт только в Hermes, Control Plane хранит только имя
`conversation`). Обе стороны НЕ равны — раздел по типу состояния, не
«один хозяин на всё» (см. ADR-001 для полного разбора, ADR-004 — почему
локальный `kanban.db`/`state.db` Hermes никогда не canonical для
Control Plane).

## Путь входящего сообщения (Telegram/MAX → ответ)

1. Вебхук приходит в `helm-core` (`api/hooks.py`) или напрямую в Hermes
   (Telegram), который ОБЯЗАН зарегистрировать сообщение в Control
   Plane ДО первого LLM-вызова — fail-closed по конструкции (ADR-013,
   §9.3).
2. Секрет/владелец проверяются, дубликат отсеивается (окно 2 минуты,
   §10.4).
3. **Knowledge Probe** (`probe.py`, fail-open) — бесплатный, локальный
   ответ первым: Z0 (extractive, точная цитата) → Z1 (список
   evidence) → Z2-рефраз (Ollama, только для Z0, только со стилем
   владельца) → если ничего не подходит, `NEEDS_REASONING`.
4. Только на `NEEDS_REASONING` — эскалация к chief-агенту (Hermes →
   LiteLLM → OpenRouter, алиас `helm-standard`). Единственный переход
   из бесплатного мира в платный, `paid_ai_used=True` фиксируется
   именно на этом шаге (§14.14, `COSTS.md`).
5. Ответ уходит через outbox (`enqueue`), доставка — канало-специфичный
   sender (`TelegramSender`/`MaxSender`).

Для **KNOWLEDGE_USER** (secondary-пользователь) шаг 4 структурно
недостижим — модули, которые собирают ответ, не импортируют ни один
модельный клиент, гарантия проверена AST-тестами, не флагом (ADR-020).

## Хранилище — Markdown + Postgres, не одно из двух (ADR-019, канонический слой)

Second Brain — `/opt/helm-knowledge`, реальные `.md`-файлы с YAML
frontmatter (§14.3, читаемо напрямую Obsidian/SFTP) + Postgres
(`knowledge_sources`/`knowledge_chunks`/`knowledge_relations`/
`knowledge_memories` и т.д. — метаданные и индексы, не BLOB текста).
Мультитенантность — `knowledge_user_id` на каждой tenant-scoped
таблице, изоляция — двойной слой: явный предикат в коде + PostgreSQL
RLS `FORCE` (ADR-030). Graphify — задуманный derived-слой поверх
`knowledge_relations`, решение принято 01.09.2026 (`ADR-019-knowledge-
canonical-markdown-graphify.md`), реализация (semantic atomizer →
L2-заметки → relations → Graphify) не начата.

## Сеть и периметр

Caddy — единственная точка входа снаружи (`helm.cmpas.ru`), TLS.
Внутренние сервисы (`postgres`, `litellm`, `helm-embed`, `ollama`,
`n8n`) слушают только на `127.0.0.1`/внутри docker-сети, наружу не
пробрасываются. `litellm` — единственный сервис с исходящим
`HTTP_PROXY`/`HTTPS_PROXY` (обход блокировки OpenRouter по IP/
датацентру через `sing-box+mieru`) — при заведении нового внутреннего
сервиса первым делом проверять `NO_PROXY` (см. `TROUBLESHOOTING.md`,
это ловилось живьём дважды).

## Секреты, бэкапы, мониторинг — отдельные документы

`SECRETS_MAP.md`, `BACKUP_RESTORE.md`, отсутствие тяжёлого стека
мониторинга в пользу Guardian — ADR-011.

## Что архитектурно НЕ начато (честно, не пропуск)

- **Milestone C** (домены: SIMPAS, продвижение, Venture, Health) — не
  начата, второй мозг к этому технически готов (хранилище/поиск/приём
  файлов работают), но наполнение доменным контентом не делалось.
- **Milestone D** (SignalAI на этом же VPS) — не начата по прямому
  требованию спеки: раньше нельзя (нужны PASS по A-C + 7 дней чистых
  метрик), архитектуры single-writer не существует ни в каком виде
  (ADR-007 честно не написан).
- **n8n** — развёрнут, но не подключён ни к чему (ADR-002 честно не
  написан).
- **Forgejo как primary** — план решён (ADR-006), выполнение не начато
  (`GIT_MIGRATION.md`).
