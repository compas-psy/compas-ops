# HELM v4.0 RESCUE FINAL
## Техническое задание на личную AI Operating System · восстановленная нормативная версия

**Дата:** 02.09.2026
**Статус:** FINAL / implementation specification
**Revision:** v4.0 RESCUE — сохраняет HELM v3.8 вне Knowledge; полностью исправляет архитектуру Второго мозга после аудита live implementation/repo
**Назначение:** документ для автономного DevOps/coding-агента, которому владелец передаёт доступ к новому VPS и необходимые секреты  
**Стартовый VPS:** **8 vCPU / 12 GB RAM / 100 GB NVMe**. Не увеличивать заранее; решение об апгрейде принимается только по фактическим метрикам после запуска  
**Критерий:** максимальная полезная автономность при минимальной цене, высокой надёжности и минимальной сложности сопровождения
**Режим первичного развёртывания:** implementation-agent работает непосредственно на VPS; Git/PR/CI не являются промежуточным механизмом установки HELM. С первого шага работа распределяется мультимодельно.

**Historical migration note (preserved from v3.8):** текущая реализация уже может выполнять v3.7 ZIP batch ingest. **Не переписывать и не откатывать её.** v3.8 добавляет Micro-Memory и user isolation поверх текущего Knowledge pipeline. Safe ZIP behavior v3.7 — нормативно сохраняется.

---


# 0A. Нормативный статус v4.0 RESCUE

Эта версия выпущена как **stop-the-line correction** после аудита фактической реализации на ветке
`claude/ai-agents-server-deployment-xdp77a` (audit baseline: `c300fb205b60e17d71a7e7524f6ed55fd7752d27`, 02.09.2026).

Она преследует две цели одновременно:

1. **не сломать уже работающий HELM** — Control Plane, Hermes, LiteLLM/OpenRouter, Guardian, Panel, MAX/Telegram,
   Forgejo/GitHub, ZIP ingest, Micro-Memory, tenancy и остальные уже сделанные части сохраняются;
2. **исправить архитектуру Knowledge**, которая ушла в обычный chunk-RAG и начала лечить структурные вопросы
   тюнингом `ts_rank`, хотя продуктовый замысел — Obsidian-подобная семантическая память с Markdown-атомами,
   Wikilinks, типизированными связями и графовым retrieval.

## 0A.1. Иерархия источников правды

Для того, **что строить**:

```text
HELM v4.0 RESCUE
> ADR, созданные после него и явно ссылающиеся на v4.0
> docs/KNOWLEDGE*.md / handoff / implementation-state
> комментарии в коде
> существующая реализация
```

Для того, **как агент имеет право работать** и кто принимает решения:

```text
CLAUDE.md
> charter/00_ORG_CHARTER.md
> HELM v4.0 RESCUE
```

Если существующий код, старый ADR или старый `docs/KNOWLEDGE*.md` противоречит этому ТЗ — **исправляется код/документ, а не ТЗ интерпретируется под код**.

Любое осознанное отступление от v4.0:

```text
SPEC_DEVIATION.md
→ точная цитата требования
→ почему невозможно/неразумно
→ варианты
→ решение владельца
```

До owner decision агент не имеет права молча выбирать другое поведение.

## 0A.2. ТЗ обязано жить в репозитории

После получения этого файла implementation-agent обязан первым документационным действием положить точную копию в:

```text
helm/tree/docs/spec/HELM_FINAL_v4.0_RESCUE_2026-09-02.md
```

и создать короткий указатель:

```text
helm/tree/docs/spec/CURRENT.md
```

с:

```text
version
filename
sha256
accepted_at
supersedes = v3.8 Knowledge requirements where conflicting
```

Нельзя снова оставлять полный нормативный spec только вне репозитория.

---


## 0A.3. Статус старого ADR-019 и Knowledge-документов

Текущий `docs/adr/ADR-019-knowledge-canonical-markdown-graphify.md` отражает промежуточный semantic-v1 эксперимент
(`slug/type/text/links`, merge-by-slug, один whole-source Ollama call) и **не является нормативным после принятия v4.0**.

Первое документальное действие rescue после коммита этой спеки:

1. обновить `ADR-019` in-place:
   - статус `SUPERSEDED / REVISED BY HELM v4.0 RESCUE`;
   - сохранить раздел `History: semantic-v1 experiment` для аудита;
   - normative decision заменить на semantic-v2 contract §14.4–§14.12;
2. обновить `docs/KNOWLEDGE.md`, `docs/KNOWLEDGE_RETRIEVAL.md`, `docs/KNOWLEDGE_MODELS.md`,
   чтобы они описывали **фактический код + v4 rescue state**, а не старые v3.4/v3.8 предпосылки;
3. `implementation-state/ROADMAP-TO-DONE.md` не использовать как product spec; это журнал исполнения и он подчинён `docs/spec/CURRENT.md`.

Нельзя оставлять рядом два активных документа с разными ответами на вопрос «как устроен Second Brain».


# 0. Главная идея

HELM состоит из нескольких слоёв, у каждого одна понятная ответственность:

**HELM — не приложение «Второй мозг».** Это личная операционная система владельца: она управляет задачами,
решениями, approvals, проектами и направлениями (СИМПАС, продвижение психолога, продукт, маркетинг, финансы,
ИТ/ИБ/разработка, Venture, Personal/Health, позже SignalAI). `HELM Knowledge` — её долговременная память и
источник evidence для этих контуров, а не самостоятельная цель системы.


```text
                         ВЛАДЕЛЕЦ
                            │
                 ┌──────────┴──────────┐
                 │                     │
             Telegram                 MAX
              primary               fallback
                 │                     │
                 └──────────┬──────────┘
                            │
                    HELM Control Plane
            tasks · policy · approvals · audit
          panel API · idempotency · outbox · routines
                            │
              ┌─────────────┴─────────────┐
              │                           │
           Hermes                        n8n
       cognitive plane             adapter plane
       agents · skills          OAuth · connectors
       research · code            external APIs
              │
              │       ┌──────────────────────────────┐
              ├──────→│ HELM Knowledge / Second Brain│
              │       │ RAW · Markdown Vault         │
              │       │ FTS · pgvector · relations   │
              │       │ Graphify derived index       │
              │       └──────────────────────────────┘
              ↓
           LiteLLM
       model control plane
              │
              ↓
          OpenRouter
      primary model provider

    HELM Panel = static frontend at helm.cmpas.ru/
    Forgejo = primary Git for ongoing product/code work
    GitHub = mirror + CI/build factory

    helm-guardian = независимый host-level watchdog
```

Главное правило:

> **Интеллект может ошибаться; состояние, права, approvals, деньги, публикации, инфраструктура и опасные действия — нет.**

Для вопросов, на которые HELM уже располагает достаточными собственными знаниями, действует второй принцип:

> **Если запрос относится к знаниям, найденным во Втором мозге, платный AI запрещён по умолчанию. HELM отвечает локально или честно сообщает, что локальных данных недостаточно. Платный AI разрешается только после явного запроса владельца на конкретный turn.**

---

# 1. Приоритеты

По убыванию:

1. безопасность данных и действий
2. корректность
3. восстановимость
4. сопровождаемость
5. стоимость
6. автономность
7. скорость

Нельзя:

- повышать автономность ценой обхода approval/policy
- снижать цену ценой опасного результата
- добавлять сервис «на будущее»
- переносить детерминированную истину в LLM prompt
- делать Hermes Kanban единственной базой состояния
- превращать n8n в reasoning-оркестратор
- превращать Control Plane во второй универсальный n8n
- включать локальные LLM без измеренной необходимости

---

# 2. Milestones

## 2.1. Milestone A — Core loop

После A HELM уже полезен.

Обязательная **полная** цепочка:

```text
Telegram
↓
Hermes chief gateway
↓
HELM pre-dispatch gate → Control Plane регистрирует task
↓
Hermes chief
↓
LiteLLM alias
↓
OpenRouter
↓
реальная cloud model
↓
LiteLLM
↓
Hermes chief
↓
Telegram
```

Дополнительно:

```text
RED action → Control Plane approval → exact executor
Guardian → monitoring/cleanup
restic → backup
restore test → PASS
```

**Важно:** Milestone A не считается готовым, если LiteLLM установлен, но через него не проходит реальный completion OpenRouter.

Ollama для A **не нужен и выключен**.

### A-DoD

Доказать:

1. обычный Telegram-вопрос получает реальный ответ модели через `Hermes → LiteLLM → OpenRouter`
2. запрос регистрируется в Control Plane до первого LLM-вызова
3. при недоступном Control Plane Hermes не исполняет задачу
4. GREEN/YELLOW task завершается
5. тестовый RED action без approval блокируется
6. тот же action после approval выполняется ровно один раз
7. LiteLLM фиксирует usage/cost
8. fallback модели проверен искусственным отказом primary
9. reboot VPS не ломает core loop
10. backup восстановлен в test environment

## 2.2. Milestone B — Interfaces & engineering

- **HELM Panel на `https://helm.cmpas.ru/` по готовому design handoff**
- Telegram Login/OIDC + passkey
- MAX fallback
- n8n Community
- Google Calendar/OAuth и другие полезные connectors
- Forgejo primary Git
- GitHub push mirror + GitHub-hosted CI
- Context7 MCP
- Claude Design MCP
- development lane до PR + CI

## 2.2.1. Milestone B.5 — HELM Knowledge / Second Brain

Milestone B.5 считается завершённым **не тогда, когда файлы разбиты на chunks**, а когда любой поддерживаемый
источник прошёл полный Knowledge lifecycle:

```text
L0 RAW
→ L1 SOURCE.md + technical chunks + FTS/pgvector
→ L2 semantic atomization полного содержания
→ Markdown micro-notes
→ canonical entities/events/facts/decisions/concepts
→ typed relations + [[wikilinks]]
→ KnowledgeGraphify derived graph
→ query router actually uses structured graph for structured questions
→ answer is traceable to source
```

Уже реализованные функции v3.7/v3.8 сохраняются:

```text
safe single-document ingest
safe ZIP batch ingest + durable child queue + exactly-one final batch notification
smart SHA dedup
GigaAM
Micro-Memory «Запомни»
original-file return
multi-user tenancy/RLS
local-only Knowledge answers
```

**Нельзя считать B.5 PASS**, пока автоматически загруженные документы не породили L2 semantic graph и пока
вопросы типа «каких врачей я посещал», «какие решения принимали по проекту X», «с кем обсуждал X» всё ещё
решаются top-k поиском по chunks.

## 2.3. Milestone C — Domains

- SIMPAS
- продвижение владельца как психолога
- Venture Studio
- Personal / Health
- domain RAG
- domain routines

## 2.4. Milestone D — SignalAI

SignalAI переносится на этот же VPS только после:

- A–C PASS
- 7 дней baseline
- отсутствия sustained resource pressure

## 2.5. Не входит в обязательный v1

- Ollama как **общий reasoning backend HELM** — не используется; отдельный локальный Ollama runtime для Knowledge Localizer обязателен в P8.5
- GigaAM входит только в P8.5 Knowledge ingest; не является общим HELM speech service
- постоянный browser-worker daemon
- n8n queue mode
- Prometheus/Grafana
- self-hosted CI runners
- full HELM multi-owner/team mode (approvals/infra/business control); **multiple isolated Knowledge-only users are included in v3.8**

Эти компоненты добавляются только по измеренной потребности.

---

# 3. Границы компонентов

| Слой | Делает | Не делает |
|---|---|---|
| **Control Plane** | canonical task state, owner identity, policy, approvals, action registry, idempotency, audit, outbox, routines, cost ledger, RAG ACL | reasoning, model selection, хранение полного raw health/chat history |
| **Hermes** | диалог, классификация, decomposition, research, coding, review, skills, memory, Kanban | canonical approvals/state, выбор concrete model, несанкционированные side effects |
| **LiteLLM** | aliases, concrete model routing, fallback, virtual keys, rate/budget limits, usage | бизнес-логика |
| **OpenRouter** | предоставляет выбранную cloud model и healthy upstream | бизнес-routing HELM |
| **n8n** | OAuth, connectors, external webhooks, визуально удобные deterministic integrations | reasoning, canonical state, RED policy |
| **Forgejo** | primary source control | CI для тяжёлых platform builds |
| **GitHub** | внешний mirror, Actions, build artifacts | primary writable Git truth |
| **HELM Panel** | показывает сохранённые факты, approvals, tasks, costs, system state; отправляет write-запросы только через Control Plane actions | LLM-вызовы, собственную policy/БД, прямые side effects |
| **HELM Knowledge** | immutable sources, v3.7 ZIP batches, Micro-Memory, local parsing/ASR, smart dedup, per-user taxonomy/index/graph, local retrieval, source lifecycle, local human-style rendering | paid inference by default, cross-user retrieval/dedup, canonical business decisions, silent mutation/deletion |
| **Guardian** | liveness, pressure, cleanup, backup checks, direct аварийные alerts | бизнес-решения и LLM reasoning |

## 3.1. Куда класть новую функцию

### Hermes

Если нужно:

- понять
- исследовать
- сравнить
- придумать
- написать текст
- написать/проверить код
- декомпозировать
- спорить/ревьюить

### Control Plane

Если это:

- identity
- canonical task state
- approval
- policy
- action parameters
- idempotency
- audit
- budget kill switch
- safety-critical exact execution

### n8n

Если:

- есть готовый OAuth/connector
- внешний webhook
- интеграционный flow удобнее видеть визуально
- перенос в Python не даёт выигрыша в безопасности

Количество n8n workflows **не лимитируется искусственно**. Ограничивается только их роль.

### HELM Panel

Если владельцу нужно:

- увидеть состояние
- изучить approval/task/cost/system detail
- подтвердить действие через passkey
- запустить разрешённое действие через тот же Control Plane action registry

Панель **не вызывает Hermes/LLM при загрузке страницы**.

### Forgejo/Git

Если это:

- продуктовый код после ввода Forgejo в эксплуатацию
- config/prompt/skill/policy, когда требуется дальнейшая история изменений
- ADR
- test

Первичное развёртывание HELM до handoff выполняется непосредственно на VPS и не блокируется Git workflow.

---

# 4. VPS, сеть и сервисы

## 4.1. Стартовая конфигурация

```text
8 vCPU
12 GB RAM
100 GB NVMe
Linux
public IPv4
Europe/Helsinki timezone для owner-facing schedules
```

**Не менять RAM/диск до измерений.**

Guardian после A–C даёт:

```text
actual idle RAM
p95 RAM
swap pressure
disk growth/day
days to threshold
SignalAI headroom
```

Только после этого может появиться рекомендация апгрейда.

## 4.2. Core services — A

```text
Caddy
PostgreSQL + pgvector
helm-core / Control Plane
LiteLLM Proxy
Hermes Agent
helm-guardian (systemd, не Docker)
restic
```

## 4.3. B

```text
HELM Panel static build (Caddy serves files; отдельного runtime-сервиса нет)
n8n Community
Forgejo
```

## 4.3.1. B.5 Knowledge runtime

```text
MarkItDown / Docling / ffmpeg       L1 extraction
GigaAM                              local speech-to-text
helm-embed                          local embeddings for technical chunks/nodes
Ollama                              local style + local semantic extraction/planning
Knowledge semantic worker          async L1→L2 atomization/backfill
PostgreSQL                          canonical nodes/edges/provenance
KnowledgeGraphify                   per-user/per-security-scope derived graph
```

Жёсткие правила:

- semantic worker и KnowledgeGraphify не получают OpenRouter/LiteLLM credentials;
- Ollama model for **atomization is benchmarked separately** from model for style rewriting;
- technical chunking и semantic atomization are separate stages;
- KnowledgeGraphify is never confused with repo-navigation `tools/graphify.py`/`graph/ops`;
- health semantic files/graph never enter general filesystem graph input;
- all heavy local jobs remain bounded by Guardian/resource policy.

## 4.4. D

```text
SignalAI — отдельный compose project/network/secrets
```

## 4.5. PostgreSQL

Один PostgreSQL server HELM, отдельные DB/users:

```text
helm
litellm
n8n
forgejo
```

Health внутри HELM дополнительно получает отдельный schema/user с ограниченными grants.

SignalAI в Milestone D сначала сохраняет собственный существующий PostgreSQL runtime. Не объединять БД одновременно с миграцией.

## 4.6. Public surface

Разрешённые firewall ports:

```text
22
80
443
```

Публично:

```text
https://helm.cmpas.ru/                HELM Panel static frontend
https://helm.cmpas.ru/auth/*          panel auth endpoints Control Plane
https://helm.cmpas.ru/api/panel/v1/*  authenticated panel API
https://helm.cmpas.ru/hooks/max       MAX webhook
https://helm.cmpas.ru/hooks/...       только явно зарегистрированные внешние webhooks
https://helm.cmpas.ru/<n8n-oauth-callback>
                                      только точный callback path, подтверждённый в P6
https://helm.cmpas.ru/guardian/status.json
                                      минимальный sanitized emergency status
https://git.cmpas.ru                  Forgejo HTTPS
```

Не публиковать:

```text
PostgreSQL
LiteLLM
Ollama local API (127.0.0.1:11434 only)
Hermes API/dashboard
Control Plane admin API
n8n editor UI
SignalAI internal DB
```

n8n editor — SSH tunnel.

Для OAuth допускается публично проксировать только требуемый callback path n8n; не открывать из-за OAuth весь editor.

Telegram в v1 работает через Hermes **long polling**, поэтому публичный Telegram webhook не нужен.

---

# 5. Каталоги

```text
/opt/helm/
├── compose/
├── control-plane/
├── guardian/
├── config/
│   ├── policies/
│   └── models/
├── skills/
├── skills-candidates/
├── prompts/
├── hermes/
├── panel/
│   ├── src/
│   ├── dist/
│   └── design-source/
├── n8n/exports/
├── scripts/
├── tests/
└── docs/

/opt/helm-state/
├── hermes/
├── workspaces/
├── kanban-snapshots/
└── temp/

/var/lib/forgejo/
/opt/signalai/

/etc/helm/
├── secrets/
├── ssh/
└── backup/

/etc/signalai/.env
```

`/opt/helm` — **рабочее дерево непосредственно на сервере**.

Во время первичного развёртывания implementation-agent редактирует его напрямую по SSH. Git не является prerequisite, rollback-механизмом или фазовым gate.

До установки Forgejo `/opt/helm` защищается phase-checkpoints + restic/hoster snapshot. После Milestone B Forgejo становится source control для дальнейшей эксплуатации; импорт `helm-infra` выполняется один раз после стабильного Milestone B и не должен тормозить initial deployment.

---

# 6. Bootstrap и secrets

## 6.1. Что владелец передаёт до старта

Через SSH/SFTP/console, **не через prompt**:

```text
/root/helm-bootstrap/
├── openrouter_api_key
├── telegram_bot_token
├── telegram_owner_id
├── knowledge_telegram_bot_token      # P8.6 secondary Knowledge users; separate bot
├── backup_credentials
├── github_token                  # можно добавить к Milestone B
├── max_bot_token                 # B
├── max_owner_id                  # B
├── context7_api_key              # B, если нужен
├── panel/
│   ├── HELM_PANEL_DESIGN_BRIEF.md
│   └── Техническое задание-handoff.zip
└── seed/
    └── psy_CLAUDE.md             # если этот файл должен стать seed для marketing skills
```

SignalAI credentials предоставляются перед D отдельно.

Если B/C credential отсутствует на A — A не блокируется.

### Обязательные design artifacts для Panel

Implementation-agent проверяет SHA256 перед распаковкой:

```text
HELM_PANEL_DESIGN_BRIEF.md
5b0b375dae441dea639236b004f00e7cba615927e0ac8c0e1887beb7fd9f0a30

Техническое задание-handoff.zip
84b1184bd76af2d4979719be963b3bba65d45ffee87ebd9ef6b9c94233d52b19

внутри handoff:
untitled/project/HELM Panel.dc.html
c62b11f01bb08779038c9abee93aa1fa262873e1a2abb83c3f114c817c2cab75
```

Если hash не совпадает — не продолжать Panel implementation с повреждённым/другим source; зафиксировать mismatch.

Для Panel Telegram Web Login текущая Telegram OIDC-конфигурация может потребовать отдельные `client_id/client_secret`, создаваемые в BotFather. Они **не выводятся из bot token**. Если их нет, агент строит всю панель и auth backend, а затем выдаёт владельцу один минимальный интерактивный шаг BotFather; остальные работы продолжаются.

## 6.2. Permissions

```bash
chmod 700 /root/helm-bootstrap
chmod 600 /root/helm-bootstrap/*
```

## 6.3. Runtime placement

```text
/etc/helm/secrets/      0600 root:root
/etc/helm/ssh/          0600 root:root
/etc/helm/backup/       0600 root:root
/etc/signalai/.env      0600 root:root
```

n8n OAuth/API credentials хранятся в n8n Credentials Store.

Обязателен постоянный:

```text
N8N_ENCRYPTION_KEY
```

Он входит в encrypted backup.

## 6.4. Generated secrets

Installer генерирует и не выводит в handoff:

```text
Postgres passwords/users
LITELLM_MASTER_KEY
Hermes API key
Hermes ↔ Control Plane HMAC/service tokens
MAX webhook secret
TELEGRAM_LOGIN_CLIENT_ID              # config value when BotFather Web Login is enabled
TELEGRAM_LOGIN_CLIENT_SECRET          # secret from BotFather
PANEL_SESSION_SIGNING_KEY
одноразовый PANEL_PASSKEY_ENROLLMENT_TOKEN (хранится только hash, TTL 30 мин)
Forgejo secret/internal token
RESTIC_PASSWORD, если не предоставлен
```

## 6.5. Разделение

- `OPENROUTER_API_KEY` видит LiteLLM, **не Hermes profiles**
- Hermes profiles получают только свои LiteLLM virtual keys
- Telegram Panel OIDC secret хранится в `/etc/helm/secrets/` и виден только Control Plane auth subsystem
- `health` не видит GitHub/Forgejo/deploy/trading credentials
- `engineering` не видит health DB credentials
- `reviewer` получает read-only tools
- SignalAI secrets не переиспользуются HELM автоматически

## 6.6. SSH

1. initial credential — только bootstrap
2. создать admin user
3. настроить key auth
4. проверить второй независимый login
5. только затем отключать password/root login, если не теряется emergency console access

## 6.7. Cleanup

```text
bootstrap value
→ runtime store
→ smoke test
→ delete bootstrap value
```

Создать `docs/SECRETS_MAP.md`:

```text
name
purpose
runtime location
owner
rotation procedure
last rotation
```

Без значений.

---

# 7. HELM Control Plane (`helm-core`)

## 7.1. Что это

Небольшой FastAPI service:

```text
FastAPI
SQLAlchemy
Alembic
PostgreSQL
```

Не отдельная AI-платформа.

Не содержит LLM reasoning.

## 7.2. Минимальные таблицы

### `tasks`

```text
id UUID PK
created_at
updated_at
origin_channel
origin_message_id
origin_owner_id
normalized_hash
domain
intent
risk_level
status
priority
budget_tier
hermes_session_id nullable
hermes_run_id nullable
parent_task_id nullable
title_redacted
```

Не хранить полный raw sensitive prompt без явной необходимости.

### `task_events`

Append-only:

```text
id
task_id
timestamp
actor
event_type
payload_redacted
correlation_id
```

DB rule запрещает UPDATE/DELETE обычному application role.

### `approvals`

```text
id
task_id
action_type
action_hash
payload_encrypted_or_reference
requested_at
expires_at
status
decided_at
decided_by
channel                telegram | panel | max | system
precondition_version
```

### `decisions`

```text
id
domain
question
decision
rationale
evidence_refs
created_at
review_at
```

### `channel_events`

```text
channel
external_message_id
owner_id
normalized_hash
received_at
task_id
```

### `outbox`

```text
id
channel
recipient
payload_reference
status
attempts
next_attempt_at
dedup_key
```

### `model_runs`

```text
timestamp
profile
alias
concrete_model
provider
input_tokens
output_tokens
cache_tokens
cost
latency
status
task_id nullable
reason_short nullable
```

Task-level cost может быть оценочным, если runtime не передаёт task correlation до LiteLLM. **Жёсткие лимиты гарантируются на system/profile уровне LiteLLM; ТЗ не притворяется, что per-task hard USD limit существует, если его нельзя доказать тестом.**

### `budget_daily`

Mirror/snapshot фактического budget state LiteLLM для панели и kill-switch:

```text
date
scope                  system | profile:<name>
spent_usd
soft_limit_usd nullable
hard_limit_usd
kill_switch_active
source_updated_at
```

LiteLLM остаётся источником фактического model spend; Control Plane хранит проверяемый snapshot для UI/audit.

### `panel_sessions`

```text
id
owner_id
created_at
expires_at
last_seen_at
revoked_at nullable
device_label nullable
```

Одна активная session по умолчанию: новая успешно завершённая Telegram+passkey session отзывает предыдущую.


### `knowledge_users`

Knowledge tenancy only; secondary users are **not** HELM owners.

```text
id UUID PK
role                    SYSTEM_OWNER | KNOWLEDGE_USER
status                  INVITED | ACTIVE | SUSPENDED | DELETED
display_name
locale
timezone
storage_quota_bytes
daily_ingest_quota_bytes nullable
allow_paid_ai boolean default false
style_profile_version
created_at
activated_at nullable
suspended_at nullable
```

Exactly one existing HELM owner is backfilled as `SYSTEM_OWNER`.

### `knowledge_channel_identities`

```text
id
knowledge_user_id
channel                 telegram_owner | telegram_knowledge | max
external_user_id
external_chat_id nullable
verified_at
is_primary
revoked_at nullable
```

Telegram identity authority = `from.id`, not an owner-entered chat id.

### `knowledge_invites`

```text
id
knowledge_user_id
token_hash
expected_external_user_id nullable
created_by
created_at
expires_at
used_at nullable
revoked_at nullable
```

Invite token is one-use and stored hashed.

### `knowledge_memories`

Micro-Memory created by `Запомни`, not a document source.

```text
id UUID
knowledge_user_id
kind                    fact | bookmark | identifier | note | preference | temporary
canonical_text
display_label nullable
payload_json
primary_domain_id nullable
security_scope
authority               owner_asserted
valid_from nullable
expires_at nullable
status                  ACTIVE | DISABLED | SUPERSEDED | EXPIRED | DELETED
supersedes_memory_id nullable
dedup_hash
origin_channel
origin_message_id
origin_kind             text | voice
graph_status
created_at
updated_at
last_used_at nullable
```

No parser/chunker job is created for a normal Micro-Memory item.

### `knowledge_user_usage`

```text
knowledge_user_id
storage_bytes
sources_count
memories_count
queued_jobs
ingest_bytes_today
updated_at
```

Used for quotas/backpressure; not billing.

### `webauthn_credentials`

```text
id
owner_id
credential_id
public_key
sign_count
created_at
last_used_at
label
revoked_at nullable
```

Private key никогда не попадает на сервер.

### `panel_enrollment_tokens`

```text
token_hash
owner_id
expires_at
used_at nullable
```

Только для **первого** passkey enrollment.

### `artifacts`

```text
task_id
type
uri
sha256
sensitivity
created_at
```

### `action_trust`

```text
action_type
current_level
supervised_success
last_incident_at
promoted_at
promoted_by
```

### `routines`

```text
id
name
schedule
profile
skill
budget_tier
enabled
last_run_at
last_status
consecutive_failures
```

### `metrics_ts`

Retention 90 дней для Guardian metrics.


### Knowledge semantic tables — v4.0

Полный нормативный контракт — §14.5. Новая semantic schema использует:

```text
knowledge_nodes
knowledge_node_mentions
knowledge_edges
knowledge_entity_aliases
knowledge_semantic_runs
```

Для health — private equivalents/adapters under `health` schema. Existing `knowledge_notes` / string-based
`knowledge_relations` from ADR-019 are **legacy semantic-v1**, not canonical target v4.0. They may remain during
migration, but new backfill must not depend on their merge-by-slug behavior.

## 7.3. Minimal internal API

Все internal endpoints требуют service auth/HMAC.

```text
POST /internal/inbound
GET  /internal/tasks/{id}
POST /internal/tasks/{id}/event
POST /internal/tasks/{id}/transition
POST /internal/tasks/{id}/classification
POST /internal/actions/propose
POST /internal/approvals/{id}/decision
POST /internal/hermes/event
POST /internal/model-run
POST /internal/ingest/calendar
POST /internal/ingest/metrics
POST /internal/ingest/health
POST /internal/knowledge/ingest
POST /internal/knowledge/remember
POST /internal/knowledge/probe
GET  /internal/knowledge/sources/{id}
GET  /internal/knowledge/memories/{id}
POST /internal/knowledge/users/resolve-channel
POST /internal/rag/search
GET  /internal/status
```

Panel/public authenticated layer:

```text
GET  /api/panel/v1/today
GET  /api/panel/v1/approvals
GET  /api/panel/v1/approvals/{id}
GET  /api/panel/v1/tasks
GET  /api/panel/v1/tasks/{id}
GET  /api/panel/v1/money
GET  /api/panel/v1/models
GET  /api/panel/v1/system
GET  /api/panel/v1/knowledge
GET  /api/panel/v1/knowledge/{source_id}
GET  /api/panel/v1/knowledge/{source_id}/download
GET  /api/panel/v1/memories
GET  /api/panel/v1/memories/{memory_id}
GET  /api/panel/v1/knowledge-users              # SYSTEM_OWNER metadata only
POST /api/panel/v1/knowledge-users/invite       # SYSTEM_OWNER
POST /api/panel/v1/knowledge-users/{id}/suspend # SYSTEM_OWNER action path
POST /api/panel/v1/actions/propose
POST /api/panel/v1/actions/{approval_id}/approve
POST /api/panel/v1/actions/{approval_id}/reject

GET  /auth/telegram/start
GET  /auth/telegram/callback
POST /auth/passkey/register/options
POST /auth/passkey/register/verify
POST /auth/passkey/assert/options
POST /auth/passkey/assert/verify
POST /auth/logout
```

Panel write endpoints не имеют собственной логики side effects: они вызывают тот же action registry/approval engine.

Knowledge download endpoint:

- never accepts raw filesystem path from client
- resolves `source_id` server-side
- checks owner/session/source status/sensitivity
- requires fresh passkey for sensitive originals
- uses `Content-Disposition: attachment`
- logs `SOURCE_DOWNLOADED`
- never exposes `/opt/helm-knowledge/...` as static Caddy directory

Public webhook handlers — отдельные routes, не expose internal API.

## 7.4. Durable execution

**DBOS Transact preferred, но не догма.**

В P2 выполнить spike:

```text
durable workflow start
→ crash процесса
→ restart
→ resume
→ wait approval
→ approval
→ exact-once fixture
→ retry transient failure
→ backup/restore
```

Проверить:

- актуальную Python/Postgres совместимость
- эксплуатационную прозрачность
- recovery
- backup/restore
- отсутствие обязательного managed DBOS service
- простоту против небольшого Postgres state machine

Если PASS — использовать DBOS/Postgres.

Если FAIL — `ADR-012` и простой Postgres mechanism:

```text
explicit state machine
transactional claims
idempotency keys
bounded retry/backoff
leases/heartbeat
systemd/Control Plane scheduler
recovery tests
```

Не добавлять новую workflow-платформу вместо DBOS только ради fallback.

## 7.5. Task state

```text
RECEIVED
→ REGISTERED
→ CLASSIFIED
→ RUNNING
→ NEEDS_APPROVAL / VERIFYING
→ DONE
```

Error states:

```text
REJECTED
EXPIRED
FAILED
ESCALATED
BLOCKED_CI
```

Worker lease:

- heartbeat регулярно
- stale lease reclaim
- default max attempts = 2
- после исчерпания → `ESCALATED`

## 7.6. Canonical truth

```text
owner instruction / approval / durable task status → Control Plane
code/config/skills/policy                          → Forgejo Git
agent attempt/detail                               → Hermes state/Kanban
n8n adapter execution                              → n8n log
model spend                                        → LiteLLM, mirrored/aggregated in Control Plane
```

---

# 8. Actions, policy и approvals

## 8.1. Levels

### GREEN

Выполняется автоматически.

Примеры:

```text
read
research
summarize
create internal draft
run routine
send owner notification
```

### YELLOW

Реверсивное внутреннее изменение разрешено автоматически, но обязательно записывается и показывается владельцу.

Примеры:

```text
create feature branch
open PR
create issue
write candidate skill
```

### RED

Физически невозможно выполнить без действующего approval.

Примеры:

```text
public publish
merge main
production deploy
spend money
legal submission
price change
delete business data
secret rotation
enable live SignalAI execution
change policy/trust
```

## 8.2. Policy file

```text
config/policies/actions.yaml
```

LLM не имеет права понизить level.

Policy дополнительно задаёт:

```text
initial_level
minimum_allowed_level
approval_ttl
required_preconditions
```

## 8.3. Action registry

```python
@action("publish_public_content", level="RED")
def publish_public_content(params, ctx):
    ctx.check_preconditions()
    ...
```

Каждый action:

- typed Pydantic input
- canonical serialization
- SHA256
- preconditions
- idempotency key
- audit
- unit test
- `title_ru` для Panel
- `panel_view`: `generic | publication | git_merge | spend | deploy` когда нужен специализированный detail renderer

Action payload **никогда не содержит secret values**. Он может содержать только credential/reference ID; реальные secrets подставляет executor из runtime vault/store.

Если действие фактически выполняется через n8n connector:

```text
Control Plane approved exact payload
→ signed internal n8n executor webhook
→ n8n sends exact payload
→ result
→ Control Plane audit
```

n8n не может изменить approval parameters.

## 8.4. Approval lifecycle

```text
Hermes proposes
→ Control Plane canonicalizes
→ stores payload/hash
→ owner approval request
→ owner decision
→ hash + identity + TTL verify
→ preconditions re-check
→ exact action
→ audit
```

Default expiry:

```text
24 h
```

`spend_money` above threshold:

```text
2 h
```

CI/deploy approval остаётся действительным 24 h, но `head_sha` и CI status проверяются непосредственно перед execution.

## 8.5. Telegram approval UX

Надёжный обязательный contract:

```text
/helm_approve <short-id>
/helm_reject <short-id>
/helm_details <short-id>
```

Команды принимает тот же chief Telegram bot через Hermes gateway plugin и **не передаёт их LLM**.

Inline buttons допускаются как UX enhancement только если текущий stable Hermes Telegram adapter/plugin API позволяет перехватить callback без patch core. Кнопки не являются условием безопасности или DoD.

## 8.6. Approval digest

В 09:00 и 18:00.

Некритичные ожидающие approvals объединяются в один digest.

Bulk «одобрить все» допускается только как серия отдельных решений по каждому `approval_id/hash`, а не общий wildcard approval.

## 8.7. Graduated trust

```text
initial level
→ N supervised successes
→ HELM предлагает promotion
→ owner approves `change_trust_level`
→ level changes no lower than minimum_allowed_level
```

Default `N=10`.

Никогда не graduate автоматически:

```text
spend_money
legal submission
delete_business_data
change_price
SignalAI live execution
secret rotation
policy change
trust change
```

Incident → rollback к initial level.

---

# 9. Telegram: точный runtime contract

## 9.0. Owner bot vs Knowledge Bot

v3.8 **не расширяет существующий Hermes Telegram gateway на посторонних пользователей**.

### Existing owner bot

Остаётся без изменения:

```text
SYSTEM_OWNER Telegram
→ Hermes chief gateway
→ helm-control pre-dispatch
→ local Knowledge path OR normal HELM path
```

`TELEGRAM_ALLOWED_USERS` для chief продолжает содержать только SYSTEM_OWNER.

Это сохраняет уже работающую/реализуемую архитектуру v3.7.

### Dedicated Knowledge Bot

Для дополнительных `KNOWLEDGE_USER` используется **один отдельный Telegram bot token**:

```text
Telegram Knowledge Bot
→ Telegram webhook
→ Control Plane /hooks/knowledge-telegram
→ local Knowledge services
```

Он:

- обслуживает всех verified secondary Knowledge users
- не запускает Hermes
- не имеет LiteLLM/OpenRouter keys
- не имеет action permissions вне own Knowledge
- принимает onboarding `/start kb_<token>`
- принимает files/ZIP/voice/Remember/recall/admin
- отвечает через durable local outbox/Telegram Bot API

Unknown user without valid invite gets no Knowledge access.

This bot is a transport adapter, not a new reasoning service.

If `KNOWLEDGE_TELEGRAM_BOT_TOKEN` is not yet supplied at P8.6:
- implementation continues fully
- agent outputs one exact BotFather step
- activation remains owner-interactive pending
- no temporary insecure sharing through chief bot is allowed

## 9.1. Только chief имеет Telegram gateway

Из пяти Hermes profiles только `chief` получает owner-bot credentials:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_ALLOWED_USERS = SYSTEM_OWNER only
```

И только `chief` запускает messaging gateway.

`business`, `engineering`, `health`, `reviewer` — worker profiles без Telegram token и без gateway process.

Это исключает конфликт одного bot token между несколькими profiles.

## 9.2. Transport

На always-on VPS:

```text
Telegram → Hermes chief long polling
```

Webhook mode не нужен.

## 9.3. Control Plane gate до LLM

Создать небольшой Hermes plugin `helm-control`.

Использовать stable `pre_gateway_dispatch` hook.

Для owner message:

1. проверить owner identity в Control Plane
2. зарегистрировать/deduplicate task
3. получить `helm_task_id`
4. выполнить local Knowledge Router/Probe (§14.12–14.14), если message eligible
5. обработать результат:

```text
LOCAL_ANSWER / DOCUMENT_REQUEST / KNOWLEDGE_ADMIN
→ transport/local action path
→ SKIP Hermes dispatch

KNOWLEDGE_INSUFFICIENT
→ local explanation + optional "Подключить внешний ИИ"
→ SKIP Hermes dispatch

EXPLICIT_PAID_OVERRIDE
→ attach minimal evidence pack
→ allow Hermes dispatch for this turn only

NO_KNOWLEDGE_HIT / LIVE_EXTERNAL / ordinary action
→ normal HELM dispatch
```

6. сохранить correlation/evidence metadata
7. разрешить Hermes dispatch **только для веток, где это явно разрешено выше**

Перед LLM через `pre_llm_call` добавить короткий trusted context:

```text
HELM_TASK_ID=<uuid>
DOMAIN_HINT=<... если уже известен>
```

Если Control Plane не отвечает:

```text
pre_gateway_dispatch → SKIP
```

LLM не вызывается.

Bot отвечает транспортным сообщением:

> HELM Control Plane недоступен. Задача не запущена.

Это fail-closed.

## 9.4. Completion sync

Hermes подписывает lifecycle event и отправляет в:

```text
POST /internal/hermes/event
```

на:

```text
agent start/end
subagent completion
relevant failure
```

Если completion event временно не доставился, reconcile routine исправляет stale task по Hermes run/session state.

---

# 10. MAX: реальный fallback

MAX не должен зависеть от n8n, иначе при падении n8n резервный канал перестаёт быть резервным.

## 10.1. Ingress

Единственный production webhook:

```text
MAX
→ https://helm.cmpas.ru/hooks/max
→ Control Plane
```

Control Plane:

1. проверяет `X-Max-Bot-Api-Secret`
2. проверяет owner ID
3. дедуплицирует
4. регистрирует task
5. выполняет local Knowledge Router/Probe
6. `LOCAL_ANSWER` / `DOCUMENT_REQUEST` / local admin / `KNOWLEDGE_INSUFFICIENT` → отвечает/действует через local path без Hermes
7. `EXPLICIT_PAID_OVERRIDE` → вызывает Hermes chief API с minimal evidence pack
8. `NO_KNOWLEDGE_HIT` / `LIVE_EXTERNAL` / ordinary action → normal Hermes path

Официальный MAX production webhook:

- HTTPS
- port 443
- `platform-api2.max.ru`
- webhook secret обязателен в HELM config

## 10.2. Hermes call from MAX

Hermes chief API server:

```text
127.0.0.1 only
authenticated
```

Control Plane использует Hermes Responses API с named conversation, например:

```text
conversation = "helm-max-owner"  # SYSTEM_OWNER; secondary MAX identities use user-scoped names if enabled
```

Это сохраняет multi-turn context внутри Hermes без хранения полного MAX transcript в Control Plane.

## 10.3. Response

```text
Hermes result
→ Control Plane outbox
→ MAX API
```

Если Hermes недоступен:

- task остаётся `REGISTERED/BLOCKED`
- owner получает transport-level сообщение, если MAX API доступен
- task retry после восстановления Hermes

## 10.4. Dedup Telegram + MAX

Same-channel:

```text
external_message_id = hard idempotency key
```

Cross-channel:

```text
same owner
+ same normalized content hash
+ different channel
+ within 2 minutes
→ same task
```

Не применять cross-channel content dedup к двум сообщениям в одном канале.

Явная команда `/force` создаёт новую task.

---


# 10.5. HELM Panel — owner control surface

Panel реализуется **строго на основании двух предоставленных owner artifacts**:

1. `HELM_PANEL_DESIGN_BRIEF.md` — функциональные/UX требования и ограничения
2. `Техническое задание-handoff.zip` → `untitled/project/HELM Panel.dc.html` — готовый Claude Design prototype/handoff

Design brief прямо определяет Panel как ежедневный mobile-first пульт, а не чат, editor prompts/skills или замену n8n UI.

## 10.5.1. Source precedence

При конфликте:

```text
HELM v4.0 security/architecture
> HELM_PANEL_DESIGN_BRIEF.md behavior/content rules
> HELM Panel.dc.html visual composition/detail
> bundled _ds design-system files
```

Причина: handoff bundle содержит более широкий СИМПАС design system; он используется как визуальный reference/import source, но **не имеет права вернуть в HELM Panel запрещённые brief'ом карточки, декоративность, брендовые greetings или иной product behavior**.

Implementation-agent:

1. распаковывает handoff в `/opt/helm/panel/design-source/`
2. читает `untitled/README.md`
3. читает `HELM Panel.dc.html` **полностью**
4. следует всем его imports и читает реально используемые файлы
5. не копирует prototype runtime architecture
6. переносит визуальный результат в production frontend

## 10.5.2. Production stack

Чтобы не добавлять runtime:

```text
React + TypeScript + Vite (build-time only)
↓
static dist/
↓
Caddy
```

Production не требует Node process.

Не добавлять отдельный Panel backend: backend = Control Plane panel API.

Не добавлять тяжёлую chart/UI library, если две требуемые визуализации проще реализуются SVG/CSS.

Panel никогда не вызывает Hermes/LiteLLM/OpenRouter для render, summaries или "insights".

## 10.5.3. URL

```text
https://helm.cmpas.ru/
```

Caddy routing:

```text
/                         static panel
/api/panel/v1/*           Control Plane
/auth/*                   Control Plane auth
/hooks/*                  explicit webhooks
/guardian/status.json     Guardian safe status
```

## 10.5.4. Sections

Production Panel обязан реализовать ровно пять navigation sections из brief:

```text
Сегодня
Одобрения
Задачи
Деньги
Система
```

Mobile navigation: bottom bar.

Desktop: same information model, left rail.

Не создавать дополнительный главный раздел без owner decision.

## 10.5.5. Read-data rule

Panel показывает факты из stored state.

**GET Panel API не должен запускать LLM.**

Data mapping:

### Сегодня

```text
status             ← Guardian + Control Plane critical state
approvals          ← approvals
money today        ← budget_daily + model_runs
expensive call     ← model_runs.reason_short
tasks              ← tasks
server             ← metrics_ts + backup/restore state
overnight done     ← tasks/task_events completed since previous 22:00
```

### Одобрения

```text
approval/action    ← approvals + action registry display metadata
full payload       ← approved/proposed payload reference
preconditions      ← action registry live checks
trust              ← action_trust
history            ← approvals/task_events
```

### Задачи

```text
groups/status      ← tasks
timeline           ← task_events
models/cost        ← model_runs linked by task_id
skills             ← task_events SKILL_USED
artifacts          ← artifacts
```

### Деньги

```text
limits/kill        ← LiteLLM actual state mirrored to budget_daily
30-day spend       ← model_runs
model matrix       ← current LiteLLM config + eval results
expensive calls    ← model_runs.reason_short
```

Every call above `helm-standard` tier must include stored `reason_short`.

### Система

```text
server             ← Guardian metrics
integrations       ← latest service/integration health
routines           ← routines
policies           ← actions.yaml + action_trust
knowledge          ← knowledge_sources + ingest/index/graph state
skills candidates  ← filesystem candidate metadata/diff
ADR                ← docs/adr
journal            ← task_events
n8n workflow rows  ← all active adapter workflows from n8n API + export timestamp; never hardcode count=4
git mirror         ← Forgejo/GitHub mirror freshness
```

Внутри `Система` добавляется вкладка **Знания**. Это не новый top-level navigation item и не меняет правило пяти главных разделов.

Every returned block includes:

```text
as_of
source
status: fresh | stale | error
```

Backend composes partial results; failure of one source does not turn the whole page into HTTP 500 when safe partial data exist.

## 10.5.5.1. Система → Знания

Внутренняя навигация:

```text
Запомненное | Документы | Архивы | Области и темы
```


Панель должна давать владельцу полный контроль над жизненным циклом Второго мозга без редактирования файлов вручную.

### Список

Колонки/строки:

```text
название / original filename
домен
тип
ingest status
knowledge status
дата загрузки
размер
parser / ASR
chunks
duplicate/version group
Graphify: ready | pending | stale | failed
updated_at
```

Фильтры:

```text
domain
type
knowledge status
ingest status
duplicate candidates
Graphify state
date
```

Поиск:

```text
title
original filename
source_id
SHA prefix
content
```

### Карточка source

Показывать:

- semantic status: `PENDING | RUNNING | READY | DEGRADED | FAILED`
- semantic coverage: processed windows / total windows
- node count / edge count / unresolved entity candidates
- semantic extractor model + version/digest
- KnowledgeGraphify state + graph revision
- last semantic rebuild/error code


- original filename + MIME + size
- source id
- binary SHA256 + normalized-content SHA256
- domain/sensitivity
- ingest timeline
- parser + extraction quality
- chunk count
- source/version/duplicate relationships
- Graphify state
- links/relations summary
- current/disabled/superseded state
- authority/current version
- valid/review dates where present
- last used in answer
- original file download

`DISABLED/ARCHIVED/SUPERSEDED` documents remain explicitly downloadable for owner review; this does not make them eligible for answers.

Для `health`, `identity_sensitive`, `client_restricted` raw-content preview в Panel **не рендерить**. Metadata можно показывать; скачивание original требует fresh passkey step-up.

### Actions

```text
knowledge.download_original     GREEN
knowledge.disable_source        YELLOW
knowledge.enable_source         YELLOW
knowledge.archive_source        YELLOW
knowledge.mark_superseded       YELLOW
knowledge.change_domain         YELLOW
knowledge.set_authority         YELLOW
knowledge.set_review_date       YELLOW
knowledge.rename_display        YELLOW
knowledge.reprocess             YELLOW
knowledge.rebuild_graph         YELLOW
knowledge.resolve_duplicate     YELLOW
knowledge.delete_source         RED
memory.edit                     YELLOW
memory.disable                  YELLOW
memory.enable                   YELLOW
memory.mark_superseded          YELLOW
memory.change_domain            YELLOW
memory.delete                   RED
knowledge_user.invite           YELLOW
knowledge_user.suspend          YELLOW
knowledge_user.resume           YELLOW
knowledge_user.change_quota     YELLOW
knowledge_user.enable_paid_ai   RED
knowledge_user.reset_passkey    YELLOW
knowledge_user.delete           RED
```

`disable_source` обязан исключить source из retrieval **немедленно**, до любого async reindex/rebuild.

После любого lifecycle write Control Plane увеличивает `knowledge_revision` и помечает затронутые derived indexes/materializations как stale. Application-level cache готовых Knowledge-ответов в v4.0 выключен (§14.21), поэтому answer-cache invalidation здесь отсутствует.

`delete_source`:
- немедленно делает source недоступным retrieval/download
- удаляет live RAW/indexes после RED approval
- оставляет audit tombstone
- encrypted backup может удерживать старую копию до retention expiry; это явно показывается в result

### No separate knowledge editor

Panel не превращается в Obsidian clone.

Разрешено:
- изменить display title
- domain/status/version relation
- reprocess/reindex
- download original

Не делать в v1 полноценный Markdown editor.


### Запомненное

Список Micro-Memory:

```text
текст/метка
kind
область
status
создано
действует до nullable
последнее использование
```

Поиск — по тексту/URL/метке.

Actions:

```text
memory.edit              YELLOW
memory.disable           YELLOW
memory.enable            YELLOW
memory.mark_superseded   YELLOW
memory.change_domain     YELLOW
memory.delete            RED
```

`Забудь` из бота по умолчанию = reversible `DISABLED`, а не physical delete.

Sensitive `identity` values:
- не показывать целиком в list preview
- detail/download/reveal requires fresh passkey


### Архивы / пакетные загрузки

Preserve v3.7:

```text
имя ZIP
область
статус
eligible / total
готово
дубликаты
ошибки/quarantine/skipped
chunks total
Graphify terminal
started/finished
```

Batch detail:
- original ZIP metadata/SHA
- safe-preflight result
- member status/source link
- retry failed
- cancel remaining
- disable only sources created by batch
- change domain only for created sources
- download original ZIP


### Области и темы

Per current user:

Domains:
- create
- rename
- describe
- merge
- deprecate
- no hard delete

Topics:
- candidate/active/merged/deprecated
- promote
- rename
- merge
- alias
- deprecate

No hardcoded taxonomy and no thousands of one-off keywords in UI.

### SYSTEM_OWNER → Пользователи

В `Система` добавить внутреннюю вкладку **Пользователи** только для SYSTEM_OWNER.

Показывать metadata, не contents:

```text
имя
роль
status
Telegram verified
storage / quota
queued jobs
last activity
Panel/passkey state
```

Actions:

```text
создать приглашение
приостановить
возобновить
изменить quota/timezone
разрешить/запретить paid AI
сбросить Panel passkey enrollment
экспортировать vault
удалить пользователя/данные (RED)
```

SYSTEM_OWNER UI не получает обычный browse/search по чужому содержимому.

Важно: root-оператор VPS технически имеет доступ к данным на диске/DB. v3.8 обеспечивает application-level tenant isolation, а не криптографическую защиту от root admin.

### Knowledge-only Panel shell

`KNOWLEDGE_USER` после Telegram OIDC/passkey входит в тот же static app, но получает только собственную Knowledge surface:

```text
Запомненное
Документы
Архивы
Области и темы
Настройки
```

Он не видит:
- Сегодня HELM
- approvals
- tasks
- money
- server/integrations/policies/journal
- users
- другие vaults

Backend RBAC, а не hidden frontend links, обеспечивает это ограничение.
## 10.5.5.2. Knowledge UI degraded behavior

Если Graphify недоступен:

- source management работает
- FTS/pgvector retrieval работает
- UI показывает `Graphify: stale/failed`
- disable/delete всё равно действует немедленно через canonical DB filters

Graphify failure не блокирует управление Vault.

## 10.5.6. Authentication

Use current Telegram Login/OIDC verified during implementation.

```text
Telegram OIDC Authorization Code + PKCE
→ server verifies token/JWKS/state/nonce
→ resolve Telegram external user id
→ resolve HELM/Knowledge principal
→ passkey assertion
→ role-scoped panel session
```

Identity mapping:

```text
Telegram id == configured SYSTEM_OWNER
→ SYSTEM_OWNER session

Telegram id maps to ACTIVE verified knowledge_channel_identity
→ KNOWLEDGE_USER session

otherwise
→ deny
```

Never trust a client-supplied role or `knowledge_user_id`.

Server-side session includes:

```text
panel_session_id
knowledge_user_id
role
created_at
expires_at
```

Session:

```text
24 h
Secure
HttpOnly
SameSite=Lax or stricter where flow allows
one active device per user by default
CSRF protection on writes
```

SYSTEM_OWNER receives full Panel. KNOWLEDGE_USER receives only own Knowledge shell; authorization is backend/RLS-enforced, not frontend-only.

## 10.5.7. First passkey enrollment

### SYSTEM_OWNER bootstrap

```text
Telegram OIDC verifies SYSTEM_OWNER
→ one-time owner bootstrap enrollment token
→ hash + TTL + unused check
→ WebAuthn credential creation
→ userVerification=required
→ public key stored
→ token permanently invalidated
```

Owner bootstrap token:
- generated during P7.5
- default TTL 30 min once activated
- shown once in secure implementation/admin console
- DB stores hash only
- never Git/log

### KNOWLEDGE_USER enrollment

Secondary user must already be ACTIVE via dedicated Knowledge Bot invite.

```text
verified Knowledge Bot identity
→ user-bound short-lived panel enrollment token
→ Telegram OIDC returns same external Telegram user id
→ token identity/TTL/unused checks
→ WebAuthn credential creation
→ Knowledge-only session
```

The enrollment token may be delivered through that user's already verified dedicated Knowledge Bot private chat.

It:
- is bound to `knowledge_user_id`
- cannot enroll another Telegram identity
- is one-use
- expires quickly
- is stored hashed

WebAuthn RP ID = `helm.cmpas.ru`, HTTPS only.

After enrollment every login requires Telegram OIDC + existing passkey assertion.

## 10.5.8. Step-up

Every panel write action requires a **fresh passkey assertion**, even within active 24h session.

Examples:

```text
approve/reject
retry/stop task
run/toggle routine
change limit
promote skill
restore test
Kanban snapshot
```

Panel then calls the same Control Plane action mechanism; it never performs the side effect directly.

После write frontend ждёт результат обычным API polling/refetch; WebSocket/SSE не требуются в v1.

No "remember step-up for 30 days".

## 10.5.8.1. Passkey challenge binding

Fresh step-up assertion must be bound server-side to the exact requested write.

Before WebAuthn assertion Control Plane creates a short-lived challenge record:

```text
session_id
action/approval id
action_hash or ordered batch hashes
random challenge
expires_at          default 60 s
used_at
```

After assertion:

- verify credential
- verify `userVerification`
- verify session
- verify challenge unused/not expired
- verify action hash still identical
- consume challenge exactly once

A passkey assertion issued for one action cannot approve another action.

For batch approval a single WebAuthn ceremony may bind to the **explicit ordered set** of approval IDs/hashes; Control Plane still records and executes each approval separately. No wildcard "approve everything".

## 10.5.8.2. Passkey recovery

### SYSTEM_OWNER

Keep root/admin recovery script:

```text
/opt/helm/scripts/panel-passkey-recover
```

It requires SSH/admin, revokes owner sessions/lost credentials, creates an owner-bound one-time enrollment token, stores only its hash/TTL and writes security audit. Owner recovery token is not automatically sent over Telegram/MAX.

### KNOWLEDGE_USER

Secondary users do not receive SSH/root recovery.

SYSTEM_OWNER action:

```text
knowledge_user.reset_passkey
```

must:
1. revoke only that user's Panel sessions/WebAuthn credentials
2. create a short-lived user-bound re-enrollment token
3. deliver only to that user's already verified dedicated Knowledge Bot identity
4. log the reset
5. never reset SYSTEM_OWNER

If Telegram identity may be compromised: suspend the user first and re-run controlled onboarding; do not issue bot-based recovery to the compromised identity.

## 10.5.9. Offline/degraded display

If Control Plane becomes unavailable **while an authenticated Panel tab is open**:

- keep last successfully loaded safe view in memory
- mark every stale block with its `as_of`
- disable all write controls
- fetch `/guardian/status.json`

If the user opens a **new unauthenticated** browser session while Control Plane is down:

- private Panel data cannot be authenticated and is not exposed
- show only neutral "HELM unavailable" shell + sanitized Guardian status

`/guardian/status.json` is deliberately public but contains only:

```text
status: ok | attention | problem
timestamp
control_plane_up
docker_up
incident_code nullable
```

No RAM, IP, versions, task names, costs, paths or secrets.

## 10.5.9.1. HTTP/browser security

Panel/API:

```text
HTTPS only
HSTS
Content-Security-Policy without unsafe-eval
frame-ancestors 'none'
Referrer-Policy: no-referrer
X-Content-Type-Options: nosniff
```

Authenticated API responses:

```text
Cache-Control: no-store
```

Static fingerprinted assets may use long immutable cache.

OIDC state/nonce/PKCE are server-validated.

Because implementation uses Authorization Code redirect flow, it does not depend on Telegram popup cross-window behavior.

## 10.5.10. Visual rules

Implement brief literally:

- mobile-first 390×844
- first Today view readable without scroll at target
- no chat widget
- no generated AI insights
- no decorative gauges/rings
- no percentage without absolute value
- no card-inside-card visual noise
- one primary action per screen
- system sans; monospace for IDs/SHA/tabular values
- light/dark follows system
- block-level stale/error state
- graph only for actual trend with sufficient data

Prototype mock values must be removed; production always uses APIs.

Не копировать mock-технические подписи буквально: например, если prototype пишет `Telegram · webhook`, production должен показать фактический transport (`long polling`).

## 10.5.10.1. Panel write actions

By P7.5 action registry includes or maps safely to:

```text
approval.approve
approval.reject
task.retry
task.stop
routine.run
routine.toggle
budget.limit_change
budget.kill_switch
skill.promote
backup.restore_test
kanban.snapshot
knowledge.download_original
knowledge.disable_source
knowledge.enable_source
knowledge.archive_source
knowledge.mark_superseded
knowledge.change_domain
knowledge.reprocess
knowledge.rebuild_graph
knowledge.resolve_duplicate
knowledge.delete_source
```

Their levels come from policy; Panel does not hardcode RED/YELLOW semantics.

`task.stop`:
- отменяет конкретный tracked Hermes run/worker через поддерживаемый current API/process handle
- не останавливает chief gateway
- если graceful cancel недоступен, ставит cancel flag и завершает только tracked worker process
- результат фиксируется в `task_events`.

## 10.5.11. Visual acceptance

Use provided source + `screens/01-today.png`.

Mandatory viewport checks:

```text
390×844
430×932
1440×900
```

Take screenshots of **production implementation** for review/evidence.

Do not depend on rendering the prototype for routine implementation; read source and provided screenshot first.

Acceptance:

- no horizontal scroll mobile
- Today target fits 390×844 under normal "all okay / 0 approvals" state
- 5 approvals/1 alert state remains usable
- text/diff in approval is readable
- keyboard/focus works desktop
- every write button requests step-up
- partial block error doesn't blank page
- no mock data remains

---

# 11. Hermes

## 11.1. Install

- latest **stable tagged** release на дату P0
- pin exact version/commit
- no `main`
- autoupdate OFF
- `hermes doctor`
- rollback package

Upgrade:

```text
snapshot relevant Hermes state
→ canary/golden eval
→ backup
→ upgrade
→ smoke
→ rollback on regression
```

## 11.2. Profiles

| Profile | Default alias | Scope | Write tools |
|---|---|---|---|
| `chief` | `helm-standard` | owner conversation, routing, orchestration | Control Plane proposals only |
| `business` | `helm-standard` | SIMPAS, marketing, venture, research | limited domain tools |
| `engineering` | `helm-code` | coding/dev coordination | Forgejo feature branches, sandbox |
| `health` | `helm-standard` | personal health | health store only |
| `reviewer` | `helm-review` | QA/security/evidence | none |

Logical CEO/CFO/Legal/InfoSec/PO roles are skills/review lenses, not persistent profiles.

## 11.3. Gateway/process rule

- one running messaging gateway: `chief`
- worker profiles run only when invoked
- never run two agent processes against the same Hermes profile home concurrently unless current Hermes worker mechanism explicitly supports that path

## 11.4. API server

Chief API server enabled:

```text
127.0.0.1
bearer key
```

Used by:

- MAX
- Control Plane routines
- internal orchestration

Never public.

## 11.5. Kanban

Использовать для:

- decomposition
- work queue
- attempts
- handoffs
- review loops

Не использовать как единственную копию:

- approvals
- owner decisions
- money
- health raw data
- SignalAI state

Каждая relevant task carries `helm_task_id`.

## 11.6. Kanban/state protection

Guardian:

- hourly snapshot
- SQLite integrity check где применимо
- WAL growth
- stale worker detection
- backup before Hermes upgrade

Corruption:

```text
stop dispatcher
→ preserve forensic copy
→ restore last good snapshot
→ reconcile with Control Plane
```

## 11.7. Concurrency

```text
NORMAL      max 3 workers
WARN        max 2
CRITICAL    no new workers
board/longhorizon max 1
browser max 1
```

## 11.8. Engineering sandbox

Coding worker:

```text
/opt/helm-state/workspaces/<task_id>/
```

- отдельный Git worktree/clone
- no `/etc/helm/secrets`
- no Docker socket by default
- no production SSH keys unless exact approved action requires them
- branch, never main

---

# 12. Web/research tools

Самостоятельный Hermes без Nous Portal всё равно должен уметь исследовать web.

Milestone A:

```text
web_search  → DDGS as zero-key baseline
web_extract → available keyless/free supported backend, verified in P4
```

Если keyless extract backend недоступен/rate-limited:

- использовать direct HTTP/browser only when needed
- не покупать новый paid tool автоматически

P4 smoke:

```text
search current public page
→ retrieve URL
→ extract readable content
→ cite/source URL in agent result
```

Paid Firecrawl/Tavily/Exa/etc. подключаются позже только с cost telemetry и evidence of benefit.

---

# 13. Skills

Production:

```text
/opt/helm/skills/
```

Candidates:

```text
/opt/helm/skills-candidates/
```

## 13.1. Skill format

```text
Purpose
When to use
When not to use
Required inputs
Method
Tools
Output contract
Quality gates
Escalation
Cost hint
```

Progressive disclosure обязателен: полный skill грузится только при использовании.

## 13.2. Initial set

```text
Common:
human-language
evidence-grading
decision-memo
critical-review
source-research
task-decomposition

Engineering:
repo-onboarding
bug-triage
tdd
code-review
security-review
release-readiness

SIMPAS:
portfolio-prioritization
po-practice
po-zapiski
po-moments
unit-economics
legal-review-russia

Psy:
psy-human-style
psy-positioning
psy-telegram-post
psy-content-plan
psy-legal-publication-check
psy-publication-quality-gate

Venture:
idea-screen
market-sizing
business-model
risk-map
go-no-go

Health:
health-data-normalization
sleep-review
activity-review
nutrition-log
doctor-question-prep
health-safety-gate
```

Не создавать остальные skills заранее.

## 13.3. Self-learning

Hermes может создавать candidate diff.

Production promotion:

```text
candidate
→ tests/eval
→ Git diff
→ policy gate
→ promote
```

Material change — RED:

- Tools
- safety gate
- legal gate
- output contract
- escalation
- common critical skill

Minor safe change — YELLOW.

---

# 14. HELM Knowledge / Memory / RAG — «второй мозг»

## 14.0. Роль Второго мозга в HELM

HELM Knowledge — **долговременная память операционной системы**, а не отдельный поисковик PDF.

Она должна одинаково поддерживать:

```text
Personal / бытовые факты
Health
СИМПАС: продукт / финансы / маркетинг / IT / ИБ / разработка
продвижение психолога
встречи / решения / люди
Venture
обучение / книги / лекции
SignalAI docs
```

Health отличается уровнем защиты, **но не моделью знаний**.

Главный пользовательский критерий:

> Я помню смысл, но не имя файла и не формулировку. Я спрашиваю естественными словами — HELM быстро вспоминает
> сущности, события, решения и отношения, собирает ответ и показывает источник.

## 14.1. Непереговорные инварианты

### K1. Никакой Lazy Consolidation

Для каждого eligible SOURCE L2 semantic atomization **обязательна**.

```text
RAW + SOURCE + chunks + embeddings
```

— это только L0/L1 и fallback retrieval, **не законченный Second Brain**.

### K2. Technical chunk ≠ knowledge atom

`KnowledgeChunk` — техническая единица поиска.

`KnowledgeNode/semantic Markdown note` — законченная смысловая единица: событие, факт, решение, концепт или
каноническая сущность.

Запрещено выдавать chunk boundaries за Obsidian-память.

### K3. Полное содержание SOURCE должно пройти atomizer

Запрещено:

```text
one Ollama call on text[:4000]
max 20 atoms for the whole source
```

Каждый structural window/section источника должен получить terminal semantic state.

### K4. Графовые вопросы идут через граф

Запросы о множествах, связях, событиях и временных срезах **не лечатся `ts_rank`**.

Например:

```text
Каких врачей я посещал в этом году?
У каких специалистов я был в августе?
С кем обсуждал проект X?
Какие решения мы принимали по ЗАПИСКАМ?
Почему мы отказались от функции Y?
```

→ structured graph path first.

FTS/pgvector remain fallback/evidence retrieval.

### K5. Typed relations обязательны

Machine-generated `[[wikilink]]` не превращается автоматически в безликое `relates_to` как единственную
семантику. У связи есть нормализованный `relation_type` и provenance.

### K6. Каждое знание прослеживается до источника

Every node/edge used in answer → exact `source_id` + page/chunk/time/span where available.

### K7. Machine-extracted ≠ owner-explicit

Если wikilink/edge написал локальный atomizer, `evidence_type=EXTRACTED`.

`OWNER_EXPLICIT` только для того, что реально написал/подтвердил пользователь.

`INFERRED` — только для связи, не сказанной буквально в source.

### K8. Второй мозг по умолчанию не тратит деньги

Knowledge path:

```text
OpenRouter calls = 0
LiteLLM paid calls = 0
```

до явного current-turn owner override.

### K9. Все домены используют один core

Нельзя строить отдельный «медицинский мозг», «проектный мозг» и т.п.

Разрешены domain hints/subtypes, но storage, atomization contract, graph, retrieval and provenance are shared.

### K10. Semantic failure не маскируется под DONE

L1 ingest может быть usable при сбое atomizer, но source получает:

```text
semantic_status=DEGRADED|FAILED
```

а не «полностью разобран».

### K11. RepoGraphify ≠ KnowledgeGraphify

```text
RepoGraphify:
  tools/graphify.py + graph/ops
  только навигация агентов по коду/документации compas-ops

KnowledgeGraphify:
  пользовательский semantic vault
  per-user/per-security-scope
  строится из L2 semantic Markdown/edges
```

Ни один документ/комментарий не имеет права писать просто «Graphify», если не ясно, какой из двух имеется в виду.

### K13. Не заменять источник резюме

L2 atoms are extracted from L1 source windows, not from a one-time summary of the whole document.
A summary may be derived later for convenience, but cannot be the sole evidence used to build canonical facts/relations.

### K12. Existing working features are preserved

Не ломать:
- single/ZIP ingest
- dedup
- GigaAM
- Micro-Memory
- original return
- tenancy/RLS
- health envelope/job queue
- FTS/pgvector fallback

## 14.2. Слои данных

```text
L0 RAW
  оригинальный файл/аудио/text snapshot
  immutable, SHA256

L1 SOURCE
  normalized Markdown/transcript
  technical chunks
  FTS
  embeddings

L2 SEMANTIC KNOWLEDGE
  canonical entities
  source-scoped events/facts/decisions/concepts
  typed edges
  Markdown micro-notes + Wikilinks
  KnowledgeGraphify

L3 ANSWER/INFERENCE
  deterministic graph/query result
  optional local stylistic rendering
  never silently becomes L2 fact
```

Truth order:

```text
live authoritative source
> RAW
> L1 extraction
> owner-confirmed L2
> extracted L2
> inferred L2
> answer-time synthesis
```

## 14.3. Input paths — что сохраняется из v3.7/v3.8

### Documents/files

```text
receive
→ RAW + SHA
→ domain/security scope
→ MarkItDown/Docling/GigaAM
→ L1 SOURCE.md
→ technical chunks + embeddings
→ semantic_run
→ L2 atomization
→ Markdown nodes/edges
→ KnowledgeGraphify
```

### ZIP

Сохраняется безопасный v3.7 pipeline:

```text
ZIP RAW
→ safe archive preflight
→ one broad domain
→ durable child jobs
→ per-member dedup
→ same child SOURCE pipeline
→ exactly-one final batch summary per completion_revision
```

Каждый eligible child обязан пройти L2; batch summary отдельно показывает:

```text
L1 ready
L2 semantic ready/degraded/failed
Graph ready/stale/failed
```

### Micro-Memory `Запомни`

Короткая память остаётся fast path и не прогоняется через document parser:

```text
Запомни ...
→ knowledge_memories
→ exact/local index
→ deterministic Markdown mirror
→ semantic link enrichment async
```

Micro-Memory может создавать/линковаться к canonical entities, но точные URL/номера/идентификаторы остаются exact values и не переписываются моделью.

## 14.4. Semantic atomization: обязательный output contract

Atomizer не возвращает «список красивых заметок». Он возвращает строго валидируемую структуру.

### 14.4.1. Processing windows

L1 разбивается на **semantic input windows**, используя структуру parser'а:

1. page/heading/table/section boundaries where available;
2. paragraph groups otherwise;
3. bounded overlap only where context is needed.

Каждый window имеет:

```text
window_id
source_id
source anchors: page / chunk ordinals / time range / char span
text_hash
status
```

**100% windows должны стать terminal**:

```text
PROCESSED
NO_KNOWLEDGE
FAILED
```

A `PROCESSED` window stores a result hash/count even when it produced zero nodes, so `NO_KNOWLEDGE` and
«model silently returned an incomplete object» are distinguishable during audits.

Никакой немой `text[:4000]` отсечки whole-source.

Если один window достигает `MAX_ATOMS_PER_WINDOW`, он считается `TRUNCATED` и автоматически делится/перезапускается; молча отбросить остальные atoms нельзя.

### 14.4.2. Structured local model output

Нормативный conceptual JSON:

```json
{
  "entities": [
    {
      "local_id": "e1",
      "entity_type": "PERSON",
      "subtype": "doctor",
      "label": "Кириченко Сергей Александрович",
      "aliases": []
    },
    {
      "local_id": "e2",
      "entity_type": "CONCEPT",
      "subtype": "medical_specialty",
      "label": "уролог",
      "aliases": ["урология"]
    }
  ],
  "atoms": [
    {
      "local_id": "a1",
      "kind": "EVENT",
      "subtype": "medical_visit",
      "title": "Приём уролога",
      "text": "Приём у Кириченко Сергея Александровича.",
      "occurred_at": "2026-08-19",
      "date_precision": "DAY"
    }
  ],
  "edges": [
    {"from": "a1", "type": "INVOLVES", "to": "e1", "role": "doctor"},
    {"from": "e1", "type": "HAS_ROLE", "to": "e2"}
  ]
}
```

Это пример формы, не hardcoded medicine schema. Тот же контракт работает для meetings/projects/purchases/learning.

### 14.4.3. Local-only

Atomizer:
- Ollama/local deterministic extraction only;
- no LiteLLM/OpenRouter;
- model selected by dedicated extraction benchmark;
- schema validation mandatory;
- invalid output → one or more bounded local repair/retry attempts;
- persistent failure → window/source semantic DEGRADED, never cloud fallback.

## 14.5. Canonical semantic schema v2

Current `KnowledgeNote` merge-by-slug + string `KnowledgeRelation` is **legacy semantic-v1**.

v4 target is a universal graph with UUID node identity.

### `knowledge_nodes`

```text
id UUID PK
knowledge_user_id UUID NOT NULL
kind              ENTITY | EVENT | FACT | DECISION | CONCEPT | DOCUMENT_REF | MEMORY_REF
subtype           string, controlled/normalized but extensible
canonical_label   text
normalized_key    nullable
primary_domain_id nullable
security_scope
occurred_at_start nullable
occurred_at_end   nullable
date_precision    DAY | MONTH | YEAR | UNKNOWN nullable
valid_from/to     nullable
status            ACTIVE | DISABLED | SUPERSEDED | QUARANTINE | DELETED
markdown_path     nullable
created_at
updated_at
```

### `knowledge_node_mentions`

Source-level provenance. One canonical entity can have many mentions.

```text
id
knowledge_user_id
node_id
source_id
window_id
chunk_id nullable
page nullable
time_start_ms/time_end_ms nullable
char_start/char_end nullable
evidence_text_hash
evidence_type      OWNER_EXPLICIT | EXTRACTED | INFERRED
confidence nullable
created_at
```

### `knowledge_edges`

```text
id
knowledge_user_id
from_node_id UUID
to_node_id UUID
relation_type
role nullable
source_id
mention_id/evidence_node_id nullable
evidence_type      OWNER_EXPLICIT | EXTRACTED | INFERRED
confidence nullable
status
created_at
```

### `knowledge_entity_aliases`

```text
knowledge_user_id
entity_node_id
alias
normalized_alias
source_id nullable
confidence nullable
```

### `knowledge_semantic_runs`

```text
id
knowledge_user_id
source_id
semantic_version
extractor_model
extractor_digest
status            PENDING | RUNNING | READY | DEGRADED | FAILED
windows_total
windows_processed
windows_failed
coverage_ratio
nodes_created
edges_created
unresolved_candidates
started_at
finished_at
error_code nullable
```

Every node/edge/mention produced by a run is bound to a `semantic_run_id` / semantic revision.
Only a revision whose run reached `READY` may become `current_semantic_revision` for that source.
Queries must never observe half-written staging nodes from a RUNNING/FAILED backfill.

### Migration rule

Legacy `knowledge_notes`/`knowledge_relations` may coexist during rescue, but:
- new v4 backfill writes semantic-v2 tables;
- query router v4 reads semantic-v2;
- legacy L2 data created by the experimental atomizer is not trusted as canonical and is quarantined/rebuilt;
- RAW/L1/chunks/memories/batches are **not** rebuilt merely because semantic schema changes.

Migration must be additive and reversible until R10:
- no destructive drop of legacy semantic-v1 tables during rescue;
- no rewrite of RAW bytes;
- no reparse of a source whose L1 extraction is already verified unless the parser itself is proven wrong;
- semantic-v2 revision switch is atomic per source.

## 14.6. Node semantics: entity ≠ atom

### Canonical entity node

Represents identity only:

```text
PERSON: Кириченко Сергей Александрович
ORGANIZATION: клиника X
PROJECT: ЗАПИСКИ
PLACE: офис ...
CONCEPT: урология
PRODUCT: ...
```

Entity Markdown **does not accumulate raw prose from every source**.

### Source-scoped atom

Represents one coherent statement/event/decision:

```text
EVENT: визит 19.08.2026
DECISION: не делать подписку в release 1
FACT: гарантия до 12.03.2027
CONCEPT: описание механизма из лекции
```

Every atom has provenance and its own stable UUID/node id.

Current implementation pattern:

```text
same slug → append text from another source into same .md
```

is forbidden for v4 source facts/events. It destroys statement-level provenance and can merge namesakes.

## 14.7. Entity resolution

Within one extraction window, local IDs resolve deterministically.

Across sources:

### Auto-merge allowed

Only when identity is strong, e.g.:
- same normalized full label + same entity type;
- explicit stable identifier from source;
- existing verified alias.

### Auto-merge forbidden

Examples:
- `Иванов` vs `Иванов А.С.` without enough evidence;
- same human name with conflicting organization/role;
- fuzzy vector similarity only.

Such cases create `ENTITY_RESOLUTION_CANDIDATE` for later local/manual resolution.

No same-name destructive merge.

Panel/bot eventually support merge/split/alias correction; source atoms remain intact either way.

## 14.8. Time is structured metadata

Date need not be its own Obsidian note, **but it must exist structurally**.

For EVENT/FACT/DECISION where source provides time:

```text
occurred_at_start/end
date_precision
valid_from/to
```

Without this, questions «в этом году», «в августе», «до X» cannot be graph queries.

Relative date extraction uses source/document/context timestamp only when resolvable. If uncertain → retain textual cue + UNKNOWN; never invent exact date.

## 14.9. Relation ontology

Base relation registry is domain-agnostic.

Minimum core:

```text
INVOLVES
HAS_ROLE
ABOUT
LOCATED_AT
PART_OF
CREATED_BY
OWNED_BY
RESULTED_IN
REASON_FOR
SUPPORTS
CONTRADICTS
SUPERSEDES
DERIVED_FROM
REFERS_TO
RELATED_TO
```

`role` may refine an edge without inventing hundreds of relation types, e.g.:

```text
EVENT --INVOLVES(role=doctor)--> PERSON
PERSON --HAS_ROLE--> CONCEPT(subtype=medical_specialty)
MEETING --INVOLVES(role=participant)--> PERSON
MEETING --ABOUT--> PROJECT
DECISION --ABOUT--> PROJECT
FACT --REASON_FOR--> DECISION
```

Unknown model-generated relation type is not blindly inserted. It is normalized to registry or becomes a candidate/`RELATED_TO` with evidence retained.

The local extractor emits only relation types allowed by a schema/registry supplied in its structured-output contract.
It does not invent arbitrary SQL field names, relation names or traversal logic. Domain-specific vocabulary belongs in
`subtype`/`role`/aliases unless a new core relation is deliberately promoted.

### Evidence semantics

```text
OWNER_EXPLICIT  owner wrote/confirmed relation
EXTRACTED       relation literally supported by source, extracted by local tool/model
INFERRED        plausible relation not literally asserted; never treated as source fact
```

Generated Wikilinks are a **rendering of canonical edges**, not the canonical meaning by themselves.

## 14.10. Markdown Vault / Obsidian contract

Every active semantic node materializes to readable Markdown.

Wikilinks use stable IDs with human aliases to avoid same-name ambiguity:

```markdown
[[person-<uuid>|Кириченко Сергей Александрович]]
[[concept-<uuid>|уролог]]
```

Example event:

```markdown
---
id: event:<uuid>
type: EVENT
subtype: medical_visit
occurred_at: 2026-08-19
source_id: <uuid>
page: 2
trust: extracted
---

# Приём уролога

Приём у [[person-...|Кириченко Сергей Александрович]].
Специализация: [[concept-...|уролог]].
```

Typed relations are also rendered in frontmatter/metadata for inspectability.

Markdown is deterministic materialization from canonical semantic DB + source evidence. Editing through Obsidian is not canonical write path in v1 unless a dedicated import/sync operation is invoked.

## 14.11. KnowledgeGraphify

### 14.11.1. Separate tool identity

`tools/graphify.py` and `graph/ops/*` remain **RepoGraphify** and never satisfy this requirement.

KnowledgeGraphify has separate code/config/path and consumes only user semantic Markdown/edges.

### 14.11.2. Input

```text
semantic-v2 nodes
+ typed edges
+ deterministic Markdown materialization
```

Not raw chunks.

### 14.11.3. Partitioning

At minimum:

```text
/opt/helm-knowledge/derived/graphify/users/<user_id>/general/
/opt/helm-knowledge/derived/graphify/users/<user_id>/health/
/opt/helm-knowledge/derived/graphify/users/<user_id>/client_restricted/
```

A graph process without permission for a scope cannot see that scope's Markdown.

### 14.11.4. Authority

Postgres nodes/edges/provenance are canonical.

KnowledgeGraphify is derived and rebuildable. It can improve multi-hop discovery, but exact structured answers must still be possible from canonical edges if Graphify is temporarily unavailable.

### 14.11.5. Completion

Every semantic READY source queues KnowledgeGraphify update.

Source UI exposes graph status separately:

```text
READY | PENDING | STALE | FAILED
```

B.5 final acceptance requires a real KnowledgeGraphify build on real user corpus, even if hot-path use remains gated by benchmark.

## 14.12. Query Router — mandatory before retrieval

No more single universal `probe → top 5 chunks` path.

Local router returns one of:

```text
MICRO_MEMORY_EXACT
DOCUMENT_REQUEST
STRUCTURED_GRAPH
GRAPH_PLUS_SEMANTIC
SEMANTIC_TEXT
KNOWLEDGE_ADMIN
LIVE_EXTERNAL
GENERAL
EXPLICIT_PAID_OVERRIDE
```

No paid model is used to classify.

If local LLM helps planning, it emits a constrained `GraphQueryPlan`, never SQL/shell.

### `GraphQueryPlan`

Conceptually:

```json
{
  "intent": "AGGREGATE",
  "target_kinds": ["PERSON", "CONCEPT"],
  "event_subtypes": ["medical_visit"],
  "relation_path": ["INVOLVES", "HAS_ROLE"],
  "date_range": {"from": "2026-01-01", "to": "2026-12-31"},
  "filters": {},
  "group_by": ["specialty", "person"]
}
```

Executor validates allowed node/edge fields and issues parameterized queries.

### Structured examples

#### «Каких врачей я посещал в этом году?»

```text
EVENT subtype=medical_visit within current year
→ INVOLVES(role=doctor) → PERSON
→ HAS_ROLE → medical_specialty
→ DISTINCT specialty + doctor
```

Presentation:

```text
уролог — Кириченко Сергей Александрович
гастроэнтеролог — ...
...
```

Specialty first, person second.

#### «Какие решения мы принимали по проекту X?»

```text
DECISION --ABOUT--> PROJECT X
→ time/order
→ source evidence
```

#### «С кем обсуждал проект X?»

```text
EVENT subtype=meeting --ABOUT--> PROJECT X
EVENT --INVOLVES(role=participant)--> PERSON
```

### Fallback

If structured graph coverage for relevant sources is incomplete:
- do not pretend graph answer is complete;
- execute a **local fallback scan/retrieval over eligible L1 chunks for the uncovered sources**;
- merge only evidence-backed additions into the answer and label internally which part came from fallback;
- tell owner locally if completeness still cannot be guaranteed;
- queue semantic repair/backfill for uncovered sources;
- never silently switch to paid AI.

## 14.13. Retrieval stack

### Exact/Micro-Memory

Identifiers, URLs, phone numbers, simple stored facts → deterministic exact path.

### Structured graph

Canonical nodes/edges/time → primary for entity/aggregate/relation questions.

### Semantic text

```text
FTS
+ pgvector
+ optional local rerank
```

for open-ended textual questions.

### Multi-hop

```text
canonical edge traversal
+ optional KnowledgeGraphify candidate expansion
```

### Fusion

When query needs both graph and text, fuse evidence after hard user/security/status filters. No technical result from one tenant/scope may enter ranking for another.

## 14.14. Answer construction

### Exact results

No Ollama rewrite for:
- URLs
- IDs/codes/plates
- exact dates/values
- original files

### Structured aggregate

First deterministic table/list from graph rows + provenance.

Optional local style renderer may make prose more natural, but protected fields/anchors are immutable.

### Open synthesis

Evidence pack → deterministic factual draft → local renderer → fidelity guard.

No cloud fallback in Knowledge mode.

### Provenance

Every material claim carries source references internally; user-facing answer shows compact sources and can expand to exact originals/pages.

## 14.15. Domain-agnostic semantics

Same storage/retrieval core. Examples:

```text
Health:
  medical_visit → doctor → specialty → clinic

Meetings:
  meeting → participants → project → decisions

SIMPAS:
  decision → project/product → reason → superseded decision

Marketing:
  idea/campaign → channel → audience → result/source

Purchases:
  purchase → product → seller → warranty

Learning:
  lecture/book → concept → author → related concept

Personal:
  fact/object → location / person / date
```

Domain skills may provide extraction hints and subtype vocabulary, but cannot create a different database/retrieval architecture.

## 14.16. Health isolation — urgent v4 repair

Repo audit 02.09.2026 found current production debt: the dry-run script documents **90 health sources whose chunks remain in `public.knowledge_chunks`**, loaded before health isolation was enabled.

Before semantic backfill:

1. backup DB + Vault;
2. inventory every health source/chunk/embedding/source file;
3. migrate sensitive health chunks/embeddings/private metadata to `health` schema;
4. remove migrated sensitive text from public only after count/hash verification;
5. move/ensure health filesystem data under an explicit private tree, for example:

```text
/opt/helm-knowledge-private/health/users/<user_id>/
  raw/
  sources/
  semantic/
  derived/graphify/
```

The exact root may differ, but it MUST be inaccessible to the general Knowledge/RepoGraphify worker account and MUST NOT be
`/opt/helm-knowledge/sources/` or the common semantic directories;
6. ensure KnowledgeGraphify health input/output is private and never scanned by general graph worker;
7. verify `helm_app` cannot read health content/nodes/edges;
8. run restore test.

Public health envelope may contain only orchestration-safe metadata needed by common durable job queue.

Health source filename/title/content/entities/topics/semantic Markdown must not leak through public tables/logs/general graph.

Repo audit found that current `atomizer.py::_note_file_path()` renders health semantic notes into the same generic
`vault_root/<type>/<slug>.md` tree as other domains. Routing the DB row to `health.*` does NOT fix this filesystem leak.
That behavior is a **BLOCKER** and must be removed before any committed health atomization/backfill.

**No full L2 backfill of health is allowed before this migration passes.**

## 14.17. Tenant isolation

Every node/mention/edge/semantic run includes `knowledge_user_id`.

Query starts with authenticated tenant filter; RLS remains defense-in-depth.

No cross-user:
- entity resolution
- dedup
- aliases
- Graphify
- style context
- answer cache
- original-file access.

## 14.18. Semantic model selection

The current `gemma2:2b` was selected for **style rephrase**, not semantic extraction. Its reuse in current atomizer is not accepted as a production model decision.

Before full backfill, run a dedicated atomization benchmark on real Russian corpus.

Minimum benchmark set:

```text
>= 12 real owner sources across available types/domains
+ >= 5 controlled non-health fixtures
+ medical documents with doctors/specialties/dates
+ meeting/project/decision fixture
+ purchase/warranty fixture
+ lecture/concept fixture
```

Compare current baseline and at least one materially stronger local candidate that fits 12GB resource gate.

Metrics:

```text
JSON/schema conformance after bounded local retry
window coverage
entity recall/precision
critical event/fact recall
relation precision
date extraction accuracy
hallucinated unsupported facts
Russian quality
latency/source
peak RAM
```

Hard gates for initial golden set:

```text
processed-window coverage                    100%
unsupported critical facts                  0
identifier/date corruption on exact fixture 0
schema-invalid terminal windows             0 after retry, otherwise source DEGRADED
critical expected entity/event recall       >= 90%
relation precision on labeled edges         >= 90%
```

Do not select model because «уже скачана» or «used by Z2».

## 14.19. Semantic lifecycle/status

L1 and L2 states are separate.

Example:

```text
parse_status=READY
chunk_index_status=READY
semantic_status=DEGRADED
graph_status=PENDING
```

Bot/Panel completion message must not collapse this into a misleading «документ полностью готов».

User-friendly example:

```text
Файл разобран и доступен для поиска.
Смысловые связи: 94% · 1 участок требует повторного разбора.
```

Batch has aggregate counts by each layer.

## 14.20. Source management and reprocessing

Disable/archive/supersede/delete immediately removes source's nodes/edges from ordinary eligibility by canonical status filter even before graph rebuild.

`reprocess semantic`:
- preserves RAW/L1;
- creates a new `semantic_version` in staging;
- validates quality;
- atomically switches current semantic revision only after PASS;
- old semantic revision retained for rollback until retention/cleanup.

Never destroy last known-good semantic graph before replacement passes.

## 14.21. Knowledge cache policy

Application-level cache of final Knowledge answers:

```text
OFF by default
not part of v1 DoD
```

Fresh tenant-scoped retrieval runs every Knowledge query.

Preserve:
- PostgreSQL/OS buffer caching;
- FTS/pgvector indexes;
- embedding persistence;
- Ollama `keep_alive` where safe;
- **LiteLLM/OpenRouter/provider caching for ordinary paid HELM path**.

Do not globally set `cache=false` because Knowledge answer cache is off.

## 14.22. Rescue migration from current semantic-v1

Current audit baseline: branch `claude/ai-agents-server-deployment-xdp77a` @ `c300fb205b60e17d71a7e7524f6ed55fd7752d27`.

Before code:

```text
FREEZE unrelated feature work
→ identify exact deployed SHA + DB migration head
→ backup DB/Vault
→ count sources/chunks/memories/legacy notes/relations
→ record health public/private placement
→ checkpoint
```

### Existing semantic-v1 output

Current experimental `knowledge_notes`/`knowledge_relations` may contain partial data from live/dry runs. Treat them as **untrusted experimental output**.

After backup:
- do not merge them blindly into v2;
- quarantine/export counts for comparison;
- rebuild semantic-v2 from trusted L1 SOURCE/chunks.

### Backfill order

```text
R0 source-of-truth + freeze
R1 health isolation migration
R2 semantic-v2 schema
R3 full-source windowing + extraction benchmark
R4 5–10 real sources dry-run/staging
R5 manual/golden evaluation
R6 small committed pilot
R7 query router + structured acceptance
R8 full corpus backfill
R9 KnowledgeGraphify build + multi-hop benchmark
R10 multi-domain acceptance
```

No full 90-document backfill before R4/R5 PASS.

## 14.23. Forbidden shortcuts

The following are explicit spec violations:

- fixing «which doctors / which decisions / who participated» primarily by `ts_rank` tuning;
- atomizing only first N characters of a document without processing the rest;
- a hard global cap that silently drops atoms from a source;
- model-generated links stored only as generic `relates_to` when typed relation is available;
- labeling model-generated links `OWNER_EXPLICIT`/`explicit_link` as if owner wrote them;
- merging all text about same slug into one growing entity file;
- treating 500+ unit tests with mocked atomizer as proof of semantic extraction quality;
- using RepoGraphify output as user Knowledge graph;
- declaring source fully READY when semantic stage failed;
- backfilling health semantics while sensitive legacy chunks remain public;
- using paid AI to rescue failed Knowledge extraction/query without owner opt-in;
- creating per-domain hardcoded retrieval systems.

## 14.24. What stays from the current code

Preserve unless a test proves a defect:

```text
RAW/source registration
safe ZIP expansion and durable children
SHA per-tenant dedup
MarkItDown/Docling/GigaAM
technical chunks
FTS + local embeddings/pgvector
Micro-Memory
original-file return
Knowledge users/RLS/quotas
health common job envelope concept
local style rephrase/fidelity guard
```

Refactor/replace specifically:

```text
atomizer.py whole-source truncation/output contract
KnowledgeNote merge-by-slug semantics
untyped model-generated wikilinks
semantic status observability
probe.py single top-k architecture for structured questions
KnowledgeGraphify missing implementation
health legacy public-content migration
```

## 14.25. Definition of Semantic Ready

A source is `SEMANTIC_READY` only if:

```text
all L1 windows terminal
coverage = 100%
no unsupported critical extraction detected by gate
canonical nodes written
canonical typed edges written
Markdown micro-notes materialized
provenance valid
security/tenant checks PASS
KnowledgeGraphify update queued or complete
```

A source may remain `L1_READY + SEMANTIC_DEGRADED`; it is searchable by FTS/pgvector but is explicitly not treated as complete graph evidence.

---

# 15. LiteLLM + OpenRouter

## 15.1. Роль LiteLLM

LiteLLM — не модель.

Это **диспетчер моделей**.

```text
Hermes: "нужен helm-standard"
↓
LiteLLM: "сейчас это GLM 5; fallback DeepSeek Pro"
↓
OpenRouter
↓
реальная модель
```

## 15.2. Единственный routing owner

Standard path:

```text
Hermes chooses alias
→ LiteLLM chooses concrete model/fallback
→ OpenRouter routes chosen model to healthy upstream
```

Hermes fallback chain на этом пути отключён.

Не давать одновременно Hermes + LiteLLM + OpenRouter менять класс модели.

**Knowledge-mode exception:** если task имеет `paid_model_allowed=false`, он вообще не попадает в LiteLLM/OpenRouter. LiteLLM не является fallback для локального Second Brain.

## 15.3. Milestone A — обязательная модельная инициализация

P3 выполняет **в таком порядке**:

1. поднять LiteLLM Proxy
2. подключить `OPENROUTER_API_KEY`
3. проверить OpenRouter connectivity
4. получить current model catalog/prices
5. проверить наличие candidate models
6. создать LiteLLM DB/user
7. создать profile virtual keys
8. создать aliases
9. выбрать **provisional primary/fallback** для `helm-router`, `helm-cheap`, `helm-standard`
10. выполнить реальный completion:
   ```text
   curl/test client → LiteLLM → OpenRouter → model → response
   ```
11. доказать usage/spend logging
12. искусственно сломать primary и доказать fallback
13. только после этого подключать Hermes

Для Milestone A ни Ollama, ни Nous Portal не требуются. Ollama устанавливается позже только в P8.5 как локальный Knowledge Localizer и не заменяет LiteLLM/OpenRouter для обычных HELM задач.

## 15.4. Hermes connection

Каждый profile смотрит на:

```text
LiteLLM OpenAI-compatible URL
```

и видит только alias + свой virtual key.

Прямой `OPENROUTER_API_KEY` в Hermes отсутствует.

## 15.5. Aliases

```text
helm-router
helm-cheap
helm-standard
helm-code
helm-review
helm-board
helm-longhorizon
```

## 15.6. Candidate matrix

IDs всегда перепроверяются P0/P3.

```yaml
helm-router:
  candidates:
    - deepseek/deepseek-v4-flash-0731
    - openai/gpt-5.6-luna

helm-cheap:
  candidates:
    - deepseek/deepseek-v4-flash-0731
    - openai/gpt-5.6-luna
    - anthropic/claude-haiku-4.5

helm-standard:
  primary_class:
    - z-ai/glm-5
    - deepseek/deepseek-v4-pro-0813
  challengers:
    - z-ai/glm-5.3
    - anthropic/claude-sonnet-5
    - openai/gpt-5.6-sol
    - openai/gpt-5.6-terra
    - openai/gpt-5.6-luna

helm-code:
  value:
    - z-ai/glm-5
    - deepseek/deepseek-v4-pro-0813
  deep:
    - z-ai/glm-5.3
    - anthropic/claude-sonnet-5
    - openai/gpt-5.6-sol
    - moonshotai/kimi-k2.7-code
  challenger:
    - openai/gpt-5.6-terra

helm-review:
  candidates:
    - z-ai/glm-5.3
    - anthropic/claude-sonnet-5
    - openai/gpt-5.6-sol
  frontier:
    - anthropic/claude-opus-5

helm-board:
  independent_candidates:
    - openai/gpt-5.6-sol
    - anthropic/claude-opus-5
  economical_dissent:
    - z-ai/glm-5.3
  fallback:
    - anthropic/claude-sonnet-5

helm-longhorizon:
  primary_challenger:
    - z-ai/glm-5.3
  frontier:
    - anthropic/claude-opus-5
  exceptional:
    - anthropic/claude-fable-5
```

Если model ID исчез/заменён:

- не падать
- найти current successor в OpenRouter catalog
- проверить capabilities
- записать substitution в `PRE-FLIGHT.md`
- включить в eval как challenger

## 15.7. Profile mapping

```text
chief        → helm-standard
business     → helm-standard
engineering  → helm-code
health       → helm-standard
reviewer     → helm-review

routing/extraction        → helm-router / helm-cheap
strategic board           → helm-board
exceptional long horizon  → helm-longhorizon
```

Reviewer по возможности не использует ту же concrete model, которая создала результат.

## 15.7.1. Task/cost correlation

Panel and cost audit require real task-level model accounting.

`helm-control` plugin attaches where current Hermes/LiteLLM APIs allow:

```text
helm_task_id
profile
alias
reason_short
```

to each model call.

After LLM response it sends usage metadata to Control Plane:

```text
task_id
alias
concrete_model
tokens
cost
latency
status
reason_short
```

If the current LiteLLM response does not expose final cost synchronously, Control Plane calculates provisional cost from the current catalog and reconciles it with LiteLLM spend telemetry later.

Reconciliation:

```text
every 5 min
LiteLLM actual spend
→ budget_daily
```

Hard budget enforcement remains in LiteLLM.

`reason_short` is mandatory before:
- `helm-board`
- `helm-longhorizon`
- explicit escalation from normal/default worker to a more expensive review/frontier path

If reason is absent, escalation is rejected or falls back to the permitted lower tier rather than producing an unexplained expensive call.

## 15.8. Cache

Не добавлять Redis только ради cache.

Initial:

- exact/in-process cache, если current LiteLLM OSS это поддерживает стабильно
- semantic cache OFF
- health cache OFF
- action/approval-related requests cache OFF
- cache key изолирован по profile/alias/model/prompt-version

Если reliable cache требует Redis — cache откладывается, Redis не становится скрытой зависимостью A.

## 15.9. Native exceptions

Допустимы:

- Claude Design MCP
- Claude Code native lane
- Codex runtime
- direct provider fallback

Только после ADR + измерения.

Direct provider fallback возможен только если соответствующий отдельный credential предоставлен. Не выдумывать fallback без ключа.

---

# 16. Model eval и стоимость

## 16.1. Bootstrap

До A-DoD после рабочего Hermes core loop:

```text
routing           20
tool calling      10
Russian/human      8
basic reasoning    8
```

Этого достаточно для provisional primary `helm-standard`.

Полный стартовый набор после B/C:

```text
routing           50
tool-calling      30
coding            20
Russian/human     20

SIMPAS              8
psy marketing       8
venture             8
health              8 synthetic/de-identified
```

## 16.2. Выбор primary

Primary = самый дешёвый model candidate, который:

- проходит quality bar
- стабильно вызывает tools
- не имеет явной latency/stability проблемы

Не считать более дорогую модель автоматически лучшей.

GLM 5 и GLM 5.3 — реальные primary-class challengers, а не декоративные строки.

## 16.3. Schedule

```text
daily   catalog/price/availability — без LLM
weekly  cheap routing/tool eval
event   major new model / ≥30% price change / regression / outage
```

Нет обязательного ежемесячного полного tournament.

## 16.4. Стоимость Hermes

Self-hosted Hermes:

```text
software license cost = 0
per profile cost      = 0
per worker cost       = 0
```

Платим за:

```text
LLM tokens
optional paid tools
VPS/backup storage
```

## 16.5. Budget tiers

```yaml
micro:    0.03
normal:   0.25
deep:     1.50
board:    3.00
critical: 8.00
```

Это planning limits, а не фальшивая гарантия per-task billing.

Hard enforcement:

- LiteLLM system daily budget
- LiteLLM per-profile budget/rate limit
- Fable/longhorizon explicit gate
- board escalation gate

Default system daily hard limit:

```text
$10
```

Owner can change it.

New task does not silently escalate budget tier.

---

# 17. n8n — adapter plane

## 17.1. Community Edition

- PostgreSQL
- queue mode OFF
- no Redis unless factual need appears
- no Enterprise source-control dependency

## 17.2. Start set

```text
Calendar Sync
Health Bridge Ingest          only when actual bridge exists
External Metrics Pull
other OAuth/connector flow    only when needed
```

MAX is **not** dependent on n8n.

## 17.3. Workflow rule

Good n8n workflow:

```text
external event/API
→ validate
→ normalize
→ call Control Plane
```

или approved side effect:

```text
signed exact action from Control Plane
→ connector
→ return exact result
```

Не хранить в n8n:

- owner decisions
- action policy
- canonical task state
- reasoning
- health conclusions

## 17.4. OAuth

Если connector требует OAuth callback:

- public Caddy route только на конкретный n8n callback endpoint
- n8n editor остаётся localhost/SSH tunnel

## 17.5. Version control

После material workflow change:

```text
n8n API export
→ normalize secret-free JSON
→ /opt/helm/n8n/exports/
→ Git
```

Nightly export — safety copy.

Credential values не экспортируются.

`N8N_ENCRYPTION_KEY` входит в backup.

Restore script обязателен.

## 17.6. Agent modification

```text
export current
→ duplicate disabled
→ change
→ fixture test
→ manual execution
→ approval if side effects changed
→ activate
→ archive prior
```

n8n MCP:

```text
read by default
write = YELLOW
```

---

# 18. Forgejo primary + GitHub mirror/CI

## 18.1. Authority

```text
Forgejo = primary writable Git
GitHub  = external mirror + Actions/build artifacts
```

`git.cmpas.ru` private by default.

Git access may use HTTPS token; отдельный public Forgejo SSH port не обязателен.

## 18.2. Repositories

```text
compas-psy/compas-voice
compas-psy/cmpas.ru
compas-psy/zapiski
compas-psy/compas-ops
compas-psy/signalAI-mobileApp
helm-infra
```

## 18.3. Existing-repo migration

Для каждого:

1. inventory default branch, tags, LFS, Actions, secrets, open PRs
2. mirror clone GitHub
3. integrity/refs check
4. create Forgejo repo
5. push full refs
6. compare refs
7. configure Forgejo built-in push mirror to GitHub
8. enable sync-on-push
9. prove mirrored exact SHA
10. prove CI
11. only then switch agent primary remote to Forgejo

**PR metadata не считается Git и автоматически не зеркалится.**

Open GitHub PRs:

- инвентаризировать
- не уничтожать force mirror
- либо завершить в GitHub до cutover
- либо recreate relevant PR in Forgejo
- URL/history сохранить в migration log

## 18.4. Mirror rule

Использовать Forgejo built-in **push mirror**.

Не писать самодельный Git sync daemon, если built-in mirror проходит тест.

GitHub mirror token:

- fine-grained
- selected repos only
- Contents write
- Workflows only if needed for mirrored workflow files

GitHub не является writable рабочим remote для Hermes после cutover.

## 18.5. CI

Проблема: PR metadata Forgejo не создаёт GitHub `pull_request` event.

Поэтому P6.5 для каждого repo проверяет workflow triggers.

CI path должен поддерживать **mirrored branch SHA**, например:

```text
Forgejo push branch
→ built-in push mirror
→ GitHub receives exact SHA
→ GitHub Actions `push`/workflow_dispatch runs
→ Control Plane/GitHub tool reads workflow run
→ verifies run.head_sha == Forgejo SHA
```

Нельзя принять green CI другого SHA.

Если existing workflow работает только на `pull_request`, аккуратно добавить safe `push`/manual exact-ref path без изменения product behavior.

## 18.6. PR/merge

Primary PR = Forgejo.

```text
feature branch
→ Forgejo PR
→ mirrored SHA CI in GitHub
→ exact SHA green
→ owner/policy merge gate
→ Forgejo main
→ GitHub mirror main
```

Не делать автоматический двусторонний main merge.

## 18.7. Backup

Forgejo:

- repos
- DB
- config
- attachments/PR metadata

→ restic offsite.

GitHub mirror — дополнительная копия, не единственный backup.

---

# 19. Development lane

```text
task
→ repo onboarding
→ plan
→ isolated worktree
→ implementation
→ deterministic lint/type/test
→ reviewer
→ push Forgejo feature branch
→ GitHub mirrored CI exact SHA
→ Forgejo PR
```

Rules:

- no direct main commit
- protected main in Forgejo
- PR = YELLOW
- merge main = RED initially
- deploy production = RED
- deploy precondition = exact SHA + current green CI
- Claude Code/Codex native lane only after benchmark/ADR

---

# 20. Domain: SIMPAS

Products:

```text
ПРАКТИКА
ЗАПИСКИ
МОМЕНТЫ
future products
```

Governance seed from `compas-ops`:

```text
Founder
→ Chief/CEO lens
→ CPO
→ PO Practice / Zapiski / Moments
```

Central lenses/skills:

```text
Marketing
Finance
Accounting
Legal
Engineering
InfoSec
Support
Analytics
```

Do not instantiate every role as persistent profile.

Migration from `compas-ops`:

- charter → Git/RAG
- current durable decisions → `decisions`
- metrics definitions → RAG/config
- human queue → current tasks where still relevant
- historical logs → archive/RAG with lower truth priority

Markdown state stops being the live task database.

---

# 21. Domain: Psychology marketing

Seed:

```text
/root/helm-bootstrap/seed/psy_CLAUDE.md
```

Если отсутствует — domain phase не выдумывает его; owner gets one minimal request.

Split into skills:

- style
- positioning
- content
- legal/publication gates
- analytics

Flow:

```text
goal
→ research
→ thesis
→ draft
→ human-style
→ legal check
→ quality gate
→ visual brief
→ approval
→ publish
→ analytics
```

Public publication = RED initially.

Image generation:

- not a core daemon
- configure Hermes-supported/direct provider only when P10 needs it
- budget and output logging required
- absence of image provider does not block text content pipeline

---

# 22. Domain: Venture Studio

```text
IDEA
→ SCREEN
→ DISCOVERY
→ DILIGENCE
→ EXPERIMENT
→ BOARD
→ GO / HOLD / NO-GO
```

Every significant claim:

```text
FACT
INFERENCE
ASSUMPTION
UNKNOWN
```

Decision pack:

- problem
- audience
- evidence
- market
- competitors/alternatives
- differentiation
- distribution
- economics
- technical feasibility
- legal/security
- risks
- MVP
- experiment
- kill criteria
- bear/base/bull
- dissent
- recommendation

`helm-board` only at BOARD or equivalent high-cost decision.

---

# 23. Domain: Personal / Health

## 23.1. Inputs

- manual Telegram/MAX log
- uploaded medical documents
- calendar
- Health Bridge when real bridge exists
- sleep/activity/food/manual metrics

## 23.2. Storage

Raw health:

- separate schema/user
- health RAG namespace
- health profile access only

General memory stores only safe high-level pointers/preferences when explicitly useful.

## 23.3. Processing

```text
ingest deterministic
→ normalize
→ store
→ trend/anomaly
→ retrieve relevant context
→ health agent
→ safety gate
→ owner recommendation
```

## 23.4. Allowed

- summarize
- compare periods
- trend detection
- conservative lifestyle/training suggestions
- doctor question preparation

## 23.5. Not autonomous

- diagnosis as fact
- medication change
- stop prescribed treatment
- override clinician
- high-risk exercise after warning signs

## 23.6. Health Connect

Do not invent server Samsung API.

If real bridge unavailable:

```text
/internal/ingest/health
+ manual/upload path
```

remain functional.

Bridge becomes separate implementation task.

---

# 24. SignalAI co-hosting

Repo:

```text
compas-psy/signalAI-mobileApp
```

SignalAI is separate personal/trading contour, not SIMPAS.

Current server topology must be inventoried from current repo/deployment, not assumed from stale docs.

Primary invariant:

```text
AT MOST ONE ACTIVE PRODUCTION WRITER/SCHEDULER/EXECUTION SET
```

## 24.1. Entry gate

Before migration:

- A–C PASS
- 7 days HELM baseline
- sustained RAM < 55%
- disk < 60%
- no material swap pressure
- Guardian forecasts safe headroom

If not — owner receives evidence and recommendation. **Do not auto-upgrade VPS.**

## 24.2. M0 Inventory

Capture without printing secret values:

- deployed SHA
- branch
- compose/services
- DB version/size/extensions
- volumes
- env variable names
- API hostname/TLS
- GitHub deploy workflow
- schedulers
- execution mode
- broker/provider mode
- resource baseline

## 24.3. M1 Backup

- DB dump
- config
- `/etc/signalai/.env` encrypted backup
- proxy config
- restore test

No cutover before restore PASS.

## 24.4. M2 Prepare new host

Start isolated:

```text
Postgres
Redis
API
```

Keep OFF:

```text
market scheduler
heavy scheduler
execution
Ollama
```

## 24.5. M3 Dry acceptance

- migrations on copied DB
- health
- read API
- outbound TLS
- market provider connectivity
- tests
- secret scan
- resource observation

## 24.6. M4 Quiesce old

Stop:

```text
execution
heavy scheduler
market scheduler
writers
```

Confirm no active jobs.

Final dump after quiesce.

## 24.7. M5 Restore / M6 Start

Restore final state.

Start:

```text
DB
Redis
API
market scheduler
heavy scheduler
```

Execution remains OFF.

## 24.8. M7 Cutover

Preserve public API hostname where possible so mobile client requires no rebuild.

Update deploy target only after new-host acceptance.

## 24.9. M8 Verify

- exact deployed SHA
- health
- market ingest freshness
- no duplicate jobs
- DB write/read
- Telegram/MAX SignalAI delivery if configured
- mobile thin client
- backup
- RAM/disk

## 24.10. M9 Execution

Only:

```text
old execution confirmed OFF
→ sandbox/roundtrip
→ owner RED approval
→ new execution ON
```

## 24.11. M10 Rollback

Old VPS retained ≥72 h.

Its schedulers/execution remain OFF.

Rollback switches writers exactly once.

SignalAI Ollama remains OFF until separate resource evidence.

---

# 25. Guardian / Infra Autopilot

## 25.1. Independence

`helm-guardian`:

- Python/systemd
- host level
- does not require Docker
- does not require Hermes
- does not require n8n
- writes Postgres metrics when available
- local fallback log when Postgres unavailable

## 25.2. Every 5 minutes

Check:

```text
disk
inodes
RAM
swap
load
Docker disk/cache
container health/restarts
Postgres
Control Plane
LiteLLM
Hermes gateway/API/state
n8n
Forgejo/mirror freshness
TLS expiry
backup age
restore-test age
SignalAI state after D
Knowledge ingest queue age + per-user fair-queue starvation after B.5
Ollama service + loaded local model/RSS after B.5
GigaAM active job/RSS after B.5
Graphify freshness/failures after B.5
per-user quota/storage pressure after B.5
```

## 25.3. Thresholds

Initial:

```text
disk WARN       70%
disk CRITICAL   82%
disk EMERGENCY  90%

RAM WARN        sustained 75%
RAM CRITICAL    sustained 88%
```

Use sustained windows, not instant spikes.

## 25.4. Degradation

```text
1 pause model eval/tournament
2 pause bulk RAG
3 stop browser work
4 reduce new Hermes workers
5 unload Ollama Knowledge model / defer GigaAM and bulk embeddings, but keep local FTS/pgvector retrieval available
6 throttle SignalAI heavy lane if safe
```

Preserve:

```text
Postgres
Control Plane
Guardian
owner messaging
Hermes chief basic path
SignalAI market-critical lane after D
```

## 25.5. Direct emergency alert

Guardian must be able to notify owner even if:

```text
Control Plane DOWN
Hermes DOWN
n8n DOWN
```

Therefore Guardian has root-only access to minimal Telegram/MAX sending credentials and calls channel APIs directly.

Guardian also atomically writes:

```text
/var/lib/helm-guardian/public-status.json
```

with only the sanitized fields defined in §10.5.9; Caddy serves it as `/guardian/status.json`.

It **never** receives inbound commands and never executes owner actions.

If channels unavailable, event goes to local durable alert log and retries.

## 25.6. Cleanup

Allowed automatic:

- dangling images
- stale BuildKit cache
- stopped disposable containers
- old unused images after retention
- expired workspaces/temp
- bounded logs
- old n8n ordinary executions

Forbidden:

```text
docker system prune -a --volumes
automatic named-volume deletion
automatic DB deletion
automatic active release/workspace deletion
```

Build cache target:

```text
3–5 GB
```

Deploy/release artifacts:

```text
current
previous
previous-2
```

Older only after health + rollback window.

Logs:

```text
Docker max-size 10–20m
max-file 3–5
```

n8n:

```text
successful ordinary executions ~7 days
failed/debug ~30 days
```

Actual values may be tuned from disk growth.

## 25.7. Forecast

Compute:

```text
disk growth/day
DB growth/day
RAG growth/day
Forgejo growth/day
SignalAI growth/day
days-to-threshold
```

Warn if critical projected <14 days.

---

# 26. Backup and restore

## 26.1. Backup

Include:

```text
HELM Postgres DBs
Control Plane config
LiteLLM config/DB
Hermes profiles/config/state/Kanban snapshots
HELM Knowledge all user RAW + Micro-Memory mirrors + Markdown Vault
knowledge metadata/relations/index tables
production skills/prompts/policies
n8n DB + exports + N8N_ENCRYPTION_KEY
Forgejo DB/repos/config after B
Panel production source/dist + WebAuthn public credentials/session schema through HELM DB
Caddy config
Guardian config
SignalAI DB/config after D
```

Do not backup:

```text
Docker cache
temp browser state
re-downloadable embedding/local-LLM/GigaAM model weights
Graphify derived output if it is fully reproducible
expired workspaces
```

## 26.2. Offsite

Encrypted restic.

Target chosen from supplied credentials:

- Yandex Disk/rclone
- S3-compatible
- another restic-compatible target

Do not create paid storage without owner approval.

## 26.3. Retention

```text
7 daily
4 weekly
6 monthly
```

## 26.4. Verification

Weekly automated:

```text
restore selected DB/config into temp environment
→ integrity check
```

Monthly:

```text
broader disaster restore rehearsal
```

Backup is not considered healthy if restore test is stale/failed.

---

# 27. Autonomous routines

Control Plane scheduler owns these routines.

LLM routine:

```text
scheduler
→ task register
→ Hermes chief API /v1/runs or /v1/responses
→ LiteLLM/OpenRouter
→ result
→ Control Plane outbox
→ Telegram/MAX
```

No-LLM routine executes directly.

Initial:

```text
07:30 daily   morning brief
09:00 daily   approval digest
18:00 daily   approval digest
23:30 daily   backup + n8n export + Forgejo mirror check
daily         OpenRouter catalog/price snapshot
Mon weekly    SIMPAS review
Fri weekly    cost/model report
weekly        cheap routing/tool eval
weekly        restore test
weekly        skill-candidate review
weekly        knowledge index integrity + orphan/broken-link report + expired-memory review
monthly       knowledge retrieval quality sample + free-answer ratio
monthly       capacity/trust recommendations
```

Routine rules:

- own budget tier
- cannot self-escalate budget
- cannot directly create RED side effect
- three consecutive failures → disable + owner alert
- new routine = YELLOW action

---

# 28. MCP and tools

## Mandatory by B

```text
Context7
Claude Design
GitHub
n8n read
internal HELM RAG
```

Forgejo is accessed primarily by Git/API/CLI; do not assume an MCP exists.

## Context7

Use for current version-specific external library/SDK/API docs.

Do not use instead of repo truth.

## Claude Design

Use official current MCP endpoint verified in P0.

Owner performs required interactive login.

Flow:

```text
brief
→ design
→ engineering
→ visual/browser verification
→ PR
```

## GitHub

Read:

- repos
- Actions
- artifacts
- workflow results

Write only where required for mirror/CI support.

Product code writes go to Forgejo.

## MCP trust

Per-profile allowlists.

Untrusted MCP:

- read-only by default
- write = YELLOW/RED according to action policy
- no secrets in prompt/tool result logs

---

# 29. Security runtime

- Hermes runs unprivileged
- Control Plane container unprivileged
- n8n unprivileged
- no Hermes Docker socket
- no secret mounts in coding workspace
- DB/network access least privilege
- internal APIs authenticated even on Docker network
- Caddy exposes only explicit routes
- secrets redacted from logs
- uploaded/web/repo content treated untrusted
- `/opt/helm-knowledge` excluded from Forgejo/GitHub remotes and Git discovery; private content leaves VPS only inside encrypted backup or explicit owner-approved action
- Telegram/MAX principal registry: SYSTEM_OWNER + verified Knowledge users; unknown principals denied
- every Knowledge source/chunk/vector/relation/memory/cache is user-scoped
- no cross-user dedup or Graphify graph

Prompt injection must never convert document instructions into owner instructions.

---

# 30. Testing

## 30.1. Core / A

- OpenRouter real completion through LiteLLM
- primary model fail → fallback
- Telegram → pre-dispatch CP register → Hermes → answer
- CP down → no LLM execution
- reboot
- RED blocked/approved exact-once
- backup restore

## 30.2. Control Plane

- same message ID → one task
- Telegram+MAX cross-channel duplicate → one task
- intentional same-channel repeat → two tasks
- action hash mismatch rejected
- expired approval rejected
- changed precondition rejected
- unauthorized user rejected
- outbox no duplicate
- trust promotion requires owner

## 30.3. Hermes

- only chief gateway owns Telegram token
- worker profiles no Telegram gateways
- profile tool isolation
- health isolation
- delegation
- worker crash/reclaim
- state snapshot/restore
- web search/extract smoke

## 30.4. LiteLLM

- aliases
- virtual keys
- OpenRouter
- usage/cost
- budget kill
- fallback
- cache isolation if cache enabled
- health request not served from shared business cache

## 30.5. MAX

- valid secret accepted
- invalid secret rejected
- multi-turn named conversation
- Hermes down → task retained
- n8n down → MAX still works
- Telegram down → MAX works

## 30.6. n8n

- OAuth callback
- connector fixture
- export/restore
- credential values absent from export
- signed Control Plane exact action cannot be altered

## 30.7. HELM Panel

- design-source hashes match
- no prototype mock data in production
- Telegram OIDC owner accepted; other Telegram ID rejected
- first passkey enrollment requires valid one-time token
- expired/reused enrollment token rejected
- passkey registration/auth works with `userVerification=required`
- session is 24 h and new active device revokes old active session
- every write requires fresh passkey step-up bound to exact action hash
- assertion replay on another action rejected
- passkey recovery revokes old sessions and requires SSH/admin
- read GET never triggers Hermes/LiteLLM
- approval full content/preconditions match stored action
- stale block preserves other blocks
- Control Plane down disables writes
- sanitized Guardian public status leaks no private metrics
- API has no-store; CSP/frame/referrer headers pass check
- 390×844 Today all-okay state fits design requirement
- mobile/desktop visual acceptance against supplied handoff

## 30.8. Git

- Forgejo branch push
- GitHub mirror exact SHA
- Actions run on exact SHA
- green older SHA not accepted
- Forgejo PR
- no direct main
- open GitHub PR migration gate
- restore Forgejo backup

## 30.8.5. HELM Knowledge — v4.0 RESCUE acceptance

### A. Preserve existing working functionality

Must remain PASS:
- single document ingest;
- ZIP safe preflight/path traversal/symlink/encrypted/nested safeguards;
- durable child jobs + reboot resume;
- per-member SHA dedup;
- exactly-one batch completion summary;
- GigaAM paths;
- Micro-Memory text/voice/expiry/recall;
- original source byte/SHA return;
- tenant isolation/RLS;
- no Knowledge paid calls without explicit override.

### B. Audit current data before migration

Record and attach evidence:

```text
deployed SHA
DB migration head
source count by domain/user/status
chunk count public + health
embedding count public + health
legacy knowledge_notes/relations count
health sources/chunks still in public
Vault path/file counts
```

### C. Health repair

For migrated health corpus prove:

```text
public readable health chunk text                  0
public readable health semantic node text          0
general Graphify health content                    0
helm_app SELECT health private content        denied
health owner search works                         PASS
source/count/hash parity before→after             PASS
restore test                                      PASS
```

### D. Full-source semantic coverage

Fixtures include short + long documents.

Prove:
- every source window terminal;
- content after char 4000 is actually atomized;
- >20 meaningful atoms in long fixture are not silently truncated;
- hitting per-window cap triggers split/retry, not drop;
- semantic status DEGRADED when unrecoverable window fails;
- L1 remains searchable when L2 degraded.

### E. Atom/node correctness

Golden documents manually label expected:
- entities;
- events/facts/decisions;
- dates;
- relations.

Targets:

```text
critical unsupported facts                 0
critical identifier/date corruption        0
critical entity/event recall               >= 90%
relation precision                         >= 90%
processed window coverage                  100%
```

### F. Provenance

Select random nodes/edges and prove each resolves to exact source + page/chunk/time/span where available.

No entity file may contain appended source prose without statement-level provenance.

### G. Entity resolution

Test:
- same full person name/type → one entity + two mentions;
- surname-only ambiguity → no unsafe auto merge;
- same name/different context → separate candidate/entities;
- aliases resolve;
- merge/split does not rewrite source atoms.

### H. Structured query golden cases — real execution path

Mandatory:

```text
Каких врачей я посещал в этом году?
У каких специалистов я был?
У каких врачей я был в августе?
Какие решения мы принимали по проекту X?
Почему мы приняли решение X?
С кем обсуждал проект X?
```

Assertions:
- router outcome = STRUCTURED_GRAPH or GRAPH_PLUS_SEMANTIC;
- answer derives from semantic nodes/edges, not FTS top-k as primary;
- date range enforced structurally;
- answer traceable;
- paid calls = 0.

For doctor query expected presentation = **specialty first**, doctor name second if known.

### I. Multi-domain

At least 4 non-identical semantic domains/classes:

```text
health visit
project meeting/decision
purchase/warranty
lecture/concept
```

Same core node/edge pipeline; no health-only retrieval implementation accepted.

### J. KnowledgeGraphify

Prove:
- RepoGraphify and KnowledgeGraphify paths/config are different;
- per-user graph;
- per-security-scope isolation;
- real semantic Markdown imported;
- graph rebuild is deterministic/reproducible enough for derived index;
- deleting Graphify output does not destroy canonical knowledge;
- multi-hop benchmark compares canonical baseline vs +KnowledgeGraphify on real cases.

### K. Zero-paid

Instrumentation must prove for all Knowledge golden cases without override:

```text
LiteLLM calls            0
OpenRouter calls         0
direct provider calls    0
paid Graphify calls      0
```

### L. Unit tests are not semantic proof

Mock-based tests prove code contracts only. Final semantic acceptance requires real local-model calls on a fixed golden corpus and live/server integration evidence.

### M. Source-of-truth consistency

Before R10 PASS:
- `docs/spec/CURRENT.md` points to v4.0 SHA;
- ADR-019 is revised/superseded, not simultaneously active with semantic-v1 rules;
- `docs/KNOWLEDGE*.md` contain no claim that chunks-only is a complete Second Brain;
- `ROADMAP-TO-DONE.md` references v4 rescue phases;
- no production comment/doc calls RepoGraphify the user Knowledge graph.

## 30.9. Security

- prompt injection
- malicious repo instruction
- malicious MCP write
- cross-domain RAG access
- secrets grep
- internal port scan
- no public DB/LiteLLM/Hermes/n8n editor

## 30.10. Guardian

Test with stopped:

```text
Docker
Hermes
n8n
Control Plane
Postgres
```

Guardian stays alive and writes local alert.

Disk pressure test does not delete named volumes.

## 30.11. SignalAI

- single-writer invariant
- no old + new scheduler overlap
- execution OFF until approval
- exact SHA
- rollback

## 30.12. Acceptance targets

```text
routing golden              ≥95%
RED bypass                   0
cross-channel duplicate exec 0
health cross-domain leak      0
approval hash bypass          0
backup restore               PASS
wrong-SHA CI accepted         0
SignalAI duplicate writers    0
Panel write without passkey      0
Panel GET causing LLM call       0
Knowledge paid leak (no override) 0
Knowledge disabled-source leak    0
Knowledge exact duplicate reparse 0
Knowledge wrong original SHA      0
```

---

# 31. Implementation phases

## 31.0. Режим работы implementation-agent
## 31.0.1. Переход с текущей реализации на v4.0 RESCUE

Audit baseline repository:

```text
repo: compas-psy/compas-ops
working branch: claude/ai-agents-server-deployment-xdp77a
observed head 02.09.2026: c300fb205b60e17d71a7e7524f6ed55fd7752d27
branch vs main at audit: 271 commits ahead / 32 behind
```

**Не rebase/merge main во время semantic rescue.** Сначала сохранить работающий baseline и исправить Knowledge на отдельной контролируемой линии; интеграцию веток делать после R10 acceptance.

Immediate stop-the-line:

1. не начинать новые B.5 features;
2. не продолжать `ts_rank` tuning как решение entity/aggregate questions;
3. не запускать full semantic backfill текущим atomizer v1;
4. закончить только уже идущую безопасную атомарную операцию;
5. зафиксировать deployed SHA, DB head, backup, corpus counts;
6. положить v4.0 в repo as source of truth;
7. выполнить rescue R0–R10 из §14.22;
8. только после R10 продолжать P9+.

Existing RAW/L1/chunks/ZIP/Micro-Memory/tenancy remain online.

### Live-server-first

Первичное развёртывание выполняется **не через Git/PR**, а непосредственно на VPS:

```text
SSH
→ inspect server
→ edit/create files under /opt/helm
→ install/configure services
→ run tests on server
→ checkpoint
→ next phase
```

Нельзя отвечать владельцу патчем/репозиторием вместо фактической установки, если SSH доступ работает.

Forgejo/GitHub появляются как **часть конечной HELM**, а не как средство доставки каждого bootstrap-шага.

### Multimodel from P0

Дорогая frontier-модель не выполняет рутинные shell/config операции.

До P3, когда локального LiteLLM ещё нет, implementation-agent использует:

1. native multi-agent/multi-model capability своей среды — preferred
2. если её нет, прямой bootstrap OpenRouter access для изолированных analysis/generation/review subtasks

Прямой OpenRouter helper **не считается полноценной заменой tool-using multi-model worker**. Если среда implementation-agent умеет запускать только одну tool-using модель, это фиксируется в `PRE-FLIGHT.md`; routine text/code generation всё равно делегируется дешёвым моделям, а механическое применение на VPS остаётся минимальным.

После P3 auxiliary model work переключается на локальный LiteLLM.

Стартовое распределение:

| Работа | Класс модели |
|---|---|
| routine inventory, log parsing, shell/config drafting | DeepSeek Flash / Luna class |
| ordinary Python/infra implementation | GLM 5 / DeepSeek Pro class |
| difficult backend/durable execution/debug | GLM 5.3 / Sonnet class |
| HELM Panel frontend | Kimi K2.7 Code / GLM 5.3 / Sonnet candidates |
| tests/reviewer | модель другой семьи; GLM 5.3/Sonnet/Sol по сложности |
| architecture/security checkpoint | Sol/Opus class |
| Fable | только unresolved long-horizon/architecture blocker |

Это не жёсткая привязка model name → role; актуальный P0 catalog может заменить кандидата.

### Orchestrator / workers

Implementation team:

```text
ORCHESTRATOR
  high-level plan, phase gates, blockers, final synthesis
        │
        ├── OPS EXECUTOR
        │     routine live server changes
        ├── BACKEND WORKER
        │     Control Plane / Guardian / integrations
        ├── FRONTEND WORKER
        │     Panel
        └── INDEPENDENT REVIEWER
              tests/security/spec compliance
```

**Только один executor одновременно меняет один live service/path.**

Parallel workers могут:
- читать
- исследовать
- писать proposed files в isolated work dir
- тестировать isolated artifacts

Но не должны одновременно редактировать один production file/container.

Working dirs:

```text
/opt/helm-state/implementation/
├── WORKPLAN.md
├── STATUS.json
├── evidence/
├── work/
└── checkpoints/
```

`STATUS.json` содержит phase/task/worker/model/status, **без secrets**.

Дополнительно вести:

```text
/opt/helm-state/implementation/MODEL_USAGE.jsonl
```

на уровне implementation work:

```text
timestamp
phase
task
worker_role
model
why_this_tier
cost_if_available
```

Это позволяет после установки доказать, что frontier-модель не делала рутинную работу. Отсутствие точного cost от внешней agent-платформы не блокирует deployment; model/tier/task должны быть записаны.

Orchestrator обновляет его после каждого meaningful task. Это заменяет необходимость владельцу писать «продолжай».

Для экономии frontier tokens orchestrator читает **summary + test evidence**, а не полные routine logs. Полный лог поднимается только при failure/security ambiguity.

### Phase checkpoints без Git

Каждая фаза:

```text
inventory
→ create pre-change checkpoint for touched files/state
→ direct server change
→ smoke/tests
→ independent review where required
→ write evidence
→ mark phase PASS
→ continue automatically
```

Checkpoint:

```text
/opt/helm-state/implementation/checkpoints/Px-<timestamp>/
manifest.json
files.tar.zst
sha256.txt
db_dump/          when relevant
```

Это локальный rollback-механизм initial deployment.

Checkpoint tar **не содержит plaintext secret files**. Для `/etc/helm/secrets`, `/etc/signalai/.env`, DB dumps с sensitive data:
- либо encrypted restic copy
- либо metadata/checksum only в локальном checkpoint
- временный plaintext dump mode `0600` удаляется сразу после encrypted backup/test

После того как restic установлен, checkpoints дополнительно входят в offsite retention до final acceptance.

Implementation-agent **не останавливается после успешной фазы, чтобы ждать команды "продолжай"**. Он идёт дальше до:
- P15 complete, или
- реально необходимого owner-interactive шага, который невозможно обойти

Если встречен owner-interactive шаг:
- выполнить все независимые задачи
- подготовить один конкретный запрос владельцу
- сохранить state
- продолжить всё, что не зависит от ответа

Каждая фаза заканчивается short evidence report, но не требует подтверждения владельца, если policy не требует RED approval.

Hoster snapshot делать **не перед каждой фазой**, а:

- до destructive host/security change
- до material DB migration
- до Forgejo authority cutover
- до SignalAI cutover

## 31.1. Input implementation-agent и первый шаг

Owner даёт implementation-agent:

```text
HOST=<IP>
USER=<user>
PASSWORD=<initial password>     # через secret/connection mechanism агента, не в markdown
DOMAIN=helm.cmpas.ru
```

и attachments:

```text
HELM_FINAL_v3.3_2026-08-27.md
HELM_PANEL_DESIGN_BRIEF.md
Техническое задание-handoff.zip
```

Также через secure secret mechanism:

```text
OPENROUTER_API_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_OWNER_ID
backup credentials when available
optional B/C secrets when already available
```

**Owner не обязан сам загружать эти файлы на VPS.**

Первый implementation task агента:

```text
1. SSH connect
2. create /root/helm-bootstrap/input and secret files with correct permissions
3. upload/copy its attached spec + panel artifacts to the VPS
4. write supplied secrets to bootstrap files without echoing values
5. verify SHA256
6. PRE-FLIGHT
7. create WORKPLAN/STATUS/MODEL_USAGE
8. start multimodel delegation
9. execute P1…P15 continuously
```

Если конкретная agent platform не умеет securely transfer attachments/secrets, только тогда owner получает **один** минимальный upload/secret instruction. Не перекладывать на владельца shell-работу, которую агент способен сделать сам.

## P0 — Preflight

Без destructive changes.

Проверить:

- VPS/DNS
- repositories/open PRs/CI
- compas-ops
- SignalAI docs/current deployment evidence
- seed files
- latest stable Hermes
- DBOS current docs
- LiteLLM current OSS features
- OpenRouter current catalog/prices/model IDs
- n8n Community current export/API/OAuth behavior
- Forgejo current push mirror
- MAX current webhook/API
- Context7
- Claude Design MCP
- Telegram OIDC current Authorization Code/PKCE
- WebAuthn current browser/server requirements
- supplied Panel design/handoff hashes and `HELM Panel.dc.html` imports

Создать:

```text
docs/PRE-FLIGHT.md
```

Если документ ниже противоречит текущему API/version, не импровизировать молча: использовать ближайший безопасный эквивалент и ADR.

## P1 — Host

- OS update
- admin user/key
- firewall
- Docker
- Caddy
- TLS
- timezone
- directories
- bounded logs
- implementation work tree `/opt/helm` + local phase-checkpoint mechanism

## P2 — Control Plane

- Postgres + pgvector
- DB/users
- schemas/migrations
- DBOS spike
- chosen durable engine
- API/auth
- owner map
- task state
- action registry with 2–3 fixture actions
- policy
- approvals
- outbox
- routines base
- tests

## P3 — LiteLLM + OpenRouter

- LiteLLM DB
- proxy localhost
- OpenRouter key
- catalog
- aliases
- virtual keys
- budgets/rate limits
- provisional primaries/fallbacks
- **real OpenRouter completion**
- **real fallback test**
- usage/cost test

P3 FAIL → не переходить P4.

## P4 — Hermes + Telegram

- pinned Hermes
- profiles
- only chief gateway
- chief API localhost
- LiteLLM custom endpoint
- `helm-control` plugin
- Telegram owner allowlist
- web search/extract baseline
- common skills
- Control Plane lifecycle sync
- one normal conversation
- compact bootstrap eval

## P5 — Guardian + backup

- Guardian systemd
- direct emergency alert
- restic
- restore test
- cleanup dry run
- forecast base
- morning brief routine

### Milestone A acceptance

Run §30.1–30.4 core subset.

## P6 — n8n

- Community
- separate Postgres DB
- `N8N_ENCRYPTION_KEY`
- editor local/SSH
- OAuth callback route
- export/restore
- one connector smoke

## P6.5 — Forgejo

- install
- `git.cmpas.ru`
- private default
- `helm-infra`
- repo migration plan
- active PR gate
- push mirror GitHub
- exact-SHA CI
- backup
- switch **product/development** remotes only after PASS
- optionally import current stable HELM infra/config for future history; this does not retroactively make Git a deployment gate

## P7 — MAX + MCP

MAX:

- webhook directly to Control Plane
- HMAC/secret test
- named Hermes conversation
- outbound response
- n8n-down test

MCP:

- Context7
- GitHub
- Claude Design
- n8n read

Owner-interactive login may remain one explicit pending step.

## P7.5 — HELM Panel

Input:
- verified `HELM_PANEL_DESIGN_BRIEF.md`
- verified/unpacked Claude Design handoff

Implement:

- production static frontend
- Control Plane panel read API
- Telegram OIDC
- WebAuthn/passkey storage/session
- first-enrollment flow
- passkey recovery script
- action-bound step-up writes
- Today/Approvals/Tasks/Money/System
- Guardian degraded status
- mobile/desktop acceptance
- no mock data
- action display metadata (`title_ru`, `panel_view`)
- model cost/reason correlation

If Telegram OIDC Client ID/Secret or BotFather Allowed URLs require owner interaction:
- finish frontend/backend first
- output exact BotFather action
- activate auth immediately after values supplied

## P8 — Development lane

Test repo:

```text
task
→ branch
→ code
→ tests
→ reviewer
→ Forgejo
→ GitHub Actions exact SHA
→ Forgejo PR
```

## P8.5 — HELM Knowledge / Second Brain · v4.0 semantic rescue

### R0 — Freeze + truth

- commit spec into `docs/spec`;
- record current working/deployed SHA;
- backup DB + Vault;
- inventory current data;
- disable/avoid semantic-v1 full backfill;
- create `implementation-state/V4.0-RESCUE-DELTA.md`.

### R1 — Health privacy debt first

- migrate existing health text/embeddings/private metadata out of public;
- isolate health L1/L2 filesystem paths;
- verify permissions/counts/hashes/restore;
- no semantic health backfill before PASS.

### R2 — Semantic-v2 schema

Implement §14.5:

```text
knowledge_nodes
knowledge_node_mentions
knowledge_edges
knowledge_entity_aliases
knowledge_semantic_runs
```

+ tenant/RLS/private health adapters.

Legacy semantic-v1 remains quarantined/read-only during migration.

### R3 — Full-source atomizer v2

- structural windows over entire SOURCE;
- constrained JSON schema;
- entities + atoms + typed edges + dates + provenance;
- bounded local retries;
- no whole-source first-4000 truncation;
- no silent atom cap.

### R4 — Dedicated local extraction benchmark

Do not reuse `gemma2:2b` by default just because it won the style test.

Benchmark extraction-specific candidates under §14.18. Pick by semantic quality + resource gate.

### R5 — Pilot semantic build

5–10 real sources, staging then small commit.

Manual/golden review before full corpus.

### R6 — Markdown v2 + entity resolution

- stable UUID wikilinks;
- canonical entity files separate from source atoms;
- aliases/candidates;
- no merge-by-slug prose append.

### R7 — Query Router + structured executor

Implement §14.12.

Must make the doctor/project/meeting golden cases use graph path.

### R8 — Full corpus backfill

Only after R4–R7 PASS.

Idempotent, resumable, per-source semantic revision. Preserve RAW/L1.

### R9 — KnowledgeGraphify

- separate from RepoGraphify;
- per-user/per-security-scope;
- build from semantic-v2 Markdown;
- real multi-hop benchmark.

### R10 — Multi-domain + regression acceptance

Run §30.8.5 in full, then independent reviewer of another model family.

Do not declare B.5 complete until R10 PASS.

## P8.6 — Multi-user Knowledge tenancy

Already implemented portions remain. Any semantic-v2 table/file/graph must inherit the same tenant isolation/RLS/quotas and dedicated Knowledge Bot boundaries. Do not reopen multi-owner HELM scope.

## P9 — SIMPAS

- migrate useful governance
- RAG
- product skills
- weekly routine

## P10 — Psychology marketing

- seed CLAUDE.md
- split skills
- style golden cases
- publication gate
- test channel

## P11 — Venture

- pipeline
- decision pack
- board gate

## P12 — Health

- isolated storage
- RAG
- manual/upload ingest
- safety gate
- actual Health Bridge only if available

### Milestone C baseline

After C, collect 7 days metrics before D.

## P13 — SignalAI

Use §24 M0–M10.

## P14 — Full acceptance + independent read

1. Run §30 complete.
2. Исправить failures.
3. Повторить до PASS.
4. Перед handoff провести **стороннее spec-vs-implementation чтение моделью другой семьи**, которая не была главным implementer этой подсистемы.
5. Reviewer ищет:
   - пропущенные requirements
   - несуществующие API assumptions
   - secret exposure
   - unreachable failover
   - duplicate ownership
   - stale/mock data
   - unnecessary services
6. Любая найденная реальная дыра исправляется и тестируется; stylistic preferences без functional benefit не расширяют scope.

## P15 — Handoff

Only after acceptance.

---



# 32. Open decisions / defaults

| ID | Decision | Default |
|---|---|---|
| D1 | Forgejo primary | **Resolved: yes** |
| D2 | Daily hard AI limit | `$10/day` |
| D3 | Task frontier approval threshold | `$3` before exceptional escalation |
| D4 | Morning/digests | `07:30 / 09:00 / 18:00 Europe/Helsinki` |
| D5 | SignalAI if 12 GB insufficient | owner decides after Guardian evidence; no auto-upgrade |
| D6 | Public channels eligible for graduated trust | none until 10 supervised successes |
| D7 | Health Bridge unavailable | manual/upload path; bridge pending |
| D8 | Claude Code/Codex native lane | after benchmark |
| D9 | Durable engine | DBOS if P2 spike PASS, otherwise Postgres fallback |
| D10 | Telegram Panel OIDC | current BotFather Client ID/Secret + Allowed URLs; if absent, one owner-interactive setup during P7.5 |

Agent should not ask these again unless evidence invalidates the default.

---

# 33. Agent implementation rules

- inspect before asking
- do not ask owner for data available in repo/docs
- never invent credential/API
- never hide a failing acceptance test
- no production deploy/merge without current policy
- no product refactor during infrastructure migration unless required for compatibility
- no general HELM local AI enablement without evidence; P8.5 Knowledge Localizer/GigaAM/local embeddings are explicitly authorized but still require resource/quality benchmark
- no secret in Git/log/handoff
- pin versions/images
- new service requires ADR
- major deviation requires ADR
- destructive change requires rollback path
- blocked owner-login step does not stop unrelated work
- do not call scaffold «done»
- do not rewrite/replace already working v3.7 ZIP ingest merely to add v3.8
- tenancy changes must be data-migration-safe and preserve owner corpus
- evidence before READY
- no manual wait/"continue" checkpoints between phases
- routine work must be delegated away from frontier orchestrator when implementation environment supports it
- all deployment changes are applied on the VPS; do not substitute GitHub PRs for installation work

Implementation-agent itself uses cheap/standard model tiers for routine ops. Frontier model only for difficult architecture/debugging with recorded reason.

---


### v4.0 Rescue-specific rules

- Read `docs/spec/CURRENT.md` + current v4.0 before any Knowledge change.
- Do not treat passing mocked tests as semantic-quality evidence.
- Do not alter `ts_rank` to satisfy structured-query acceptance unless graph path already PASS and a separate text-search defect remains.
- Do not silently choose implementation convenience over §14 invariants.
- Do not reuse RepoGraphify to claim KnowledgeGraphify completion.
- Do not run a full semantic backfill until extraction benchmark/pilot pass.
- Do not expose health semantic Markdown to general filesystem/graph worker.
- Any spec ambiguity: stop and surface exact clause + alternatives.

# 34. Required ADRs

```text
ADR-001 Hermes cognitive / Control Plane canonical state
ADR-002 n8n adapter boundary
ADR-003 LiteLLM routing ownership
ADR-004 Hermes state/Kanban non-canonical
ADR-005 Health isolation
ADR-006 Forgejo primary / GitHub mirror+CI
ADR-007 SignalAI single-writer migration
ADR-008 Skills promotion
ADR-009 Action registry / RED executor
ADR-010 Graduated trust
ADR-011 No heavy monitoring stack in v1
ADR-012 Durable execution DBOS spike decision
ADR-013 Telegram pre-dispatch Control Plane gate
ADR-014 MAX direct Control Plane ingress
ADR-015 HELM Panel static frontend + Control Plane backend
ADR-016 Panel Telegram OIDC + WebAuthn
ADR-017 Live-server-first initial deployment
ADR-018 Multimodel implementation policy
ADR-019 Knowledge canonical Markdown/Postgres; Graphify derived
ADR-020 Strict zero-paid Knowledge lock
ADR-021 Local parser/GigaAM/Ollama Knowledge pipeline
ADR-022 Smart source dedup + versioning
ADR-023 Knowledge lifecycle management Panel/bot
ADR-024 Scalable dynamic Knowledge taxonomy
ADR-025 Global-within-user hybrid retrieval
ADR-026 Safe ZIP batch ingest + exactly-once completion
ADR-027 Micro-Memory «Запомни» fast path
ADR-028 Knowledge tenant model: SYSTEM_OWNER vs KNOWLEDGE_USER
ADR-029 Dedicated Knowledge Telegram Bot + invite/principal verification
ADR-030 Tenant isolation: user key + RLS + no cross-user dedup
ADR-031 Per-user fair queue/quotas/style isolation
```

---

# 35. Handoff

Create:

```text
docs/HANDOFF.md
docs/ARCHITECTURE.md
docs/OPERATIONS.md
docs/SECRETS_MAP.md
docs/BACKUP_RESTORE.md
docs/MODEL_ROUTING.md
docs/MCP.md
docs/GIT_MIGRATION.md
docs/PANEL.md
docs/PANEL_AUTH_RECOVERY.md
docs/SIGNALAI_MIGRATION.md
docs/ROUTINES.md
docs/TRUST_LEDGER.md
docs/COSTS.md
docs/TROUBLESHOOTING.md
docs/KNOWLEDGE.md
docs/KNOWLEDGE_INGEST.md
docs/KNOWLEDGE_RETRIEVAL.md
docs/KNOWLEDGE_MODELS.md
docs/KNOWLEDGE_ADMIN.md
docs/KNOWLEDGE_MIGRATION_V3.5.md
docs/TEST_REPORT.md
docs/D-LIST.md
```

Handoff contains:

- URLs
- exact installed versions
- service status
- current model aliases/primaries/fallbacks
- current budgets
- current RAM/disk baseline
- last backup
- last restore test
- pending owner-interactive steps
- known limitations

Без secret values.

Panel handoff дополнительно включает:
- production frontend source/dist
- копию исходного design brief
- SHA256 исходного Claude Design handoff
- auth enrollment/recovery procedure
- screenshots acceptance viewports


---

# 36. Final Definition of Done

HELM готов, если:

```text
Owner writes normal Telegram message
↓
Control Plane registers it
↓
local router decides:

KNOWLEDGE
→ local retrieval → local style/original document → answer
→ 0 paid calls unless explicit override

GENERAL/LIVE/explicit paid
→ Hermes → LiteLLM → OpenRouter → useful answer
→ cost visible
```

и одновременно:

- HELM Panel at `helm.cmpas.ru/` matches supplied design/brief and shows only factual stored state
- Panel authentication = Telegram OIDC + passkey; every write uses fresh passkey step-up
- MAX independently works when Telegram/n8n are unavailable
- RED cannot execute without valid approval
- exact approved parameters cannot be substituted
- Forgejo is primary Git
- GitHub mirrors exact commits and runs CI on exact SHA
- backups are restorable
- owner documents/audio/text enter HELM Knowledge with immutable RAW provenance
- filename is never the dedup key; SHA/content/version logic is active
- any material Knowledge hit locks paid AI to **OFF** unless owner explicitly opts in for that turn
- local textual knowledge answers are rendered in owner style by local Ollama and pass fidelity guard
- ambiguous/conflicting Knowledge answers remain local and transparent; no silent cloud escalation
- original-document requests return the original bytes/SHA, not a reconstruction
- owner can disable/archive/version/reprocess/delete knowledge from Panel and bot
- disabled/deleted knowledge cannot leak through cache/vector/Graphify
- GigaAM performs audio transcription locally/on-demand
- Graphify is derived enrichment; core retrieval survives without it
- v3.7 ZIP batch ingest remains working with exactly-once aggregate completion
- `Запомни` text/voice creates a Micro-Memory without document parsing and recalls locally
- exact URL/identifier recall is byte/text exact and never generatively altered
- temporal Micro-Memory expires from current recall without being destroyed
- multiple Knowledge users can be invited through a dedicated Knowledge Bot with verified Telegram `from.id`
- secondary Knowledge users cannot reach Hermes/full HELM by default
- every Knowledge query/cache/vector/graph/download is tenant-scoped
- same content in two users never creates a cross-user duplicate signal
- per-user style/Graphify/cache isolation is proven
- user suspend/export/delete lifecycle is documented/tested
- health data are isolated
- production skills cannot silently self-modify
- Guardian works without Docker/Hermes/n8n/Control Plane
- VPS remains **8 vCPU / 12 GB / 100 GB** until real metrics justify a change
- SignalAI, after D, has exactly one production writer/scheduler/execution set
- initial deployment was completed directly on VPS with server-side evidence/checkpoints rather than Git gating
- implementation log demonstrates multimodel delegation: frontier model was not used for routine ops unless cheaper workers were unavailable/failed

---


Additional v4.0 Knowledge DoD:

```text
Second Brain is not chunk-RAG only
all eligible sources receive complete L2 semantic processing or explicit DEGRADED status
semantic Markdown micro-notes + stable Wikilinks exist
canonical typed nodes/edges/provenance exist
structured entity/aggregate/time questions use graph path first
KnowledgeGraphify built from user semantic vault, separate from RepoGraphify
health old public chunks migrated before health semantic backfill
0 paid AI on Knowledge without explicit per-turn override
all domains share same semantic core
```

# 37. Current documentation to verify in P0

The implementation agent must verify current docs rather than trust model memory:

- Hermes Agent: `https://hermes-agent.nousresearch.com/docs/`
- Hermes Telegram: `https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram`
- Hermes hooks: `https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks`
- Hermes API server: `https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server`
- Hermes web search: `https://hermes-agent.nousresearch.com/docs/user-guide/features/web-search`
- DBOS Python: `https://docs.dbos.dev/python/`
- LiteLLM: `https://docs.litellm.ai/`
- OpenRouter: `https://openrouter.ai/`
- n8n: `https://docs.n8n.io/`
- Forgejo mirrors: `https://forgejo.org/docs/latest/user/repo-mirror/`
- Context7: `https://github.com/upstash/context7`
- Claude Design: current official Anthropic documentation
- supplied `HELM_PANEL_DESIGN_BRIEF.md`
- supplied Claude Design handoff bundle (`HELM Panel.dc.html` + imports)
- MAX API: `https://dev.max.ru/docs-api`
- Docling: current official docs / Context7 `/docling-project/docling`
- Microsoft MarkItDown: current official docs / Context7 `/microsoft/markitdown`
- Unstructured: current official docs only as fallback candidate
- GigaAM: `https://github.com/salute-developers/GigaAM`
- Graphify: `https://graphify.com/docs`
- Ollama local API/models: `https://docs.ollama.com/` and current Ollama model library
- BGE-M3: official Hugging Face model card
- multilingual-e5-base: official Hugging Face model card
- Telegram Login/OIDC: `https://core.telegram.org/bots/telegram-login`
- WebAuthn: current W3C Web Authentication specification
- Vite/React current stable docs if selected frontend versions changed

Snapshot date of this specification: **27.08.2026**.

---


v4.0 addition:

```text
helm/tree/docs/spec/CURRENT.md
helm/tree/docs/spec/HELM_FINAL_v4.0_RESCUE_2026-09-02.md
```

Older `docs/KNOWLEDGE*.md` and ADR-019 must be updated/marked superseded where they conflict with semantic-v2.

# 38. One rule when in doubt

```text
Need remember a fact/link?       → Micro-Memory fast path (no document parser)
Need entity/event/decision relation? → semantic-v2 graph path, NOT top-k chunks
Need remembered knowledge?      → user-scoped HELM Knowledge Probe first
Knowledge hit?                  → local-only answer / original source; paid AI LOCKED
Secondary Knowledge user?       → Knowledge-only local path; no Hermes by default
Need cloud on Knowledge?         → only explicit owner per-turn override
Need thought outside Knowledge?  → Hermes
Need paid model?                 → LiteLLM → OpenRouter
Need canonical truth/safety?     → Control Plane
Need owner control UI?          → HELM Panel → Control Plane
Need external connector/OAuth?   → n8n
Need ongoing versioned code?     → Forgejo
Need CI/build platform?          → GitHub
Need host survival?              → Guardian
```

If a new component does not clearly improve one of these responsibilities, do not add it.
