# Решения по архитектуре (ADR)

Заведён 30.08.2026 по решению учредителя. До этого нумерация ADR в
репозитории разошлась со спекой: под одним и тем же номером у нас и в
`HELM_FINAL_v3.8` лежали разные темы, и по номеру из ТЗ нельзя было
найти документ.

## Правило нумерации

**001–031 — заповедник спеки.** Эти номера закреплены за темами из §34
ТЗ и ни за чем другим. Пустой номер означает «решение ещё не написано»,
а не «номер свободен».

**100 и дальше — наши собственные решения.** То, что мы решали по ходу
работы и чего в §34 нет вовсе.

Заглушек по старым путям намеренно не оставлено. Заглушка с именем
`ADR-019-...` снова заняла бы ровно тот номер, который мы освобождаем
для спеки, — то есть воспроизвела бы ту самую путаницу, ради устранения
которой всё и затевалось. Единственная точка входа — таблица ниже.

## Что куда переехало 30.08.2026

| Было | Стало | Почему |
|---|---|---|
| `ADR-019-timezone-moscow` | `ADR-101` | Часового пояса в §34 нет. Под 019 в ТЗ — канонический Markdown/Postgres |
| `ADR-020-max-responses-api` | `ADR-014` | Это и есть тема §34 ADR-014 «MAX direct Control Plane ingress», просто стояла не под своим номером |
| `ADR-021-knowledge-attachment-transport` | `ADR-102` | Темы «транспорт вложений» в §34 нет. Под 021 в ТЗ — локальный парсер/GigaAM/Ollama |
| `ADR-024-zip-batch-ingest` | `ADR-026` | Это тема §34 ADR-026 «Safe ZIP batch ingest», стояла не под своим номером |
| `ADR-025-dedicated-knowledge-telegram-bot` | `ADR-029` | Это тема §34 ADR-029, стояла не под своим номером |

`ADR-009`, `ADR-012`, `ADR-017` и `ADR-018` остались на своих номерах:
их темы совпадают с §34. У 017 и 018 решение принято **противоположное**
тому, что предполагала спека, — это зафиксированные отклонения, а не
несовпадение тем.

## Состояние по §34

| № | Тема по ТЗ | Состояние |
|---|---|---|
| 001 | Hermes cognitive / Control Plane canonical state | ✅ `ADR-001-hermes-control-plane-canonical-state.md` |
| 002 | n8n adapter boundary | не написан — **действительно не решено**, не только не задокументировано: n8n развёрнут (127.0.0.1-only), но ни одного коннектора не заведено, `helm_core` его вообще не импортирует. Писать ADR не из чего |
| 003 | LiteLLM routing ownership | ✅ `ADR-003-litellm-routing-ownership.md` — статическая часть; динамическая эскалация по типу вопроса отложена, см. документ |
| 004 | Hermes state/Kanban non-canonical | ✅ `ADR-004-hermes-state-non-canonical.md` — синтез из нескольких мест, не одна прямая цитата, см. оговорку в документе |
| 005 | Health isolation | ✅ `ADR-005-health-isolation.md` — generic public envelope + security-scope private payload schema, реализовано в коде/тестах (P12); живой прогон `scripts/setup-health-role.sh` на сервере ещё не выполнен |
| 006 | Forgejo primary / GitHub mirror+CI | ✅ `ADR-006-forgejo-primary-github-mirror.md` — план и целевая архитектура решены, сама миграция репозиториев (шаги 2-11) не выполнена |
| 007 | SignalAI single-writer migration | не написан — **действительно не решено**: Milestone D не начата по требованию самой спеки (раньше нельзя), архитектуры single-writer не существует ни в каком виде |
| 008 | Skills promotion | ✅ `ADR-008-skills-promotion.md` — гейт (policy YAML) решён, исполнитель не зарегистрирован, `skills/` пуст |
| 009 | Action registry / RED executor | ✅ `ADR-009-action-registry.md` |
| 010 | Graduated trust | ✅ `ADR-010-graduated-trust.md` — схема и инварианты защищены на уровне БД, живой цикл повышения доверия не подключён к боевому пути |
| 011 | No heavy monitoring stack in v1 | ✅ `ADR-011-no-heavy-monitoring-v1.md` |
| 012 | Durable execution DBOS spike decision | ✅ `ADR-012-durable-execution.md` |
| 013 | Telegram pre-dispatch Control Plane gate | ✅ `ADR-013-telegram-pre-dispatch-gate.md` |
| 014 | MAX direct Control Plane ingress | ✅ `ADR-014-max-responses-api.md` |
| 015 | HELM Panel static frontend + Control Plane backend | ✅ `ADR-015-panel-static-frontend.md` |
| 016 | Panel Telegram OIDC + WebAuthn | ✅ `ADR-016-panel-telegram-webauthn.md` — реализовано другое: Telegram Login Widget, не OIDC (задокументированное отклонение, OIDC недоступен у BotFather для этого бота) |
| 017 | Live-server-first initial deployment | ✅ `ADR-017-offline-build.md` — отклонено, выбрана офлайн-сборка |
| 018 | Multimodel implementation policy | ✅ `ADR-018-single-agent.md` — отклонено фактически |
| 019 | Knowledge canonical Markdown/Postgres; Graphify derived | 🟡 `ADR-019-knowledge-canonical-markdown-graphify.md` — решение принято 01.09.2026 (E13 пересмотрен, корпус реальный: 90 health-источников), реализация (semantic atomizer, backfill, per-user Graphify, Knowledge Router) не начата, план — в самом документе |
| 020 | Strict zero-paid Knowledge lock | ✅ `ADR-020-strict-zero-paid-knowledge-lock.md` |
| 021 | Local parser/GigaAM/Ollama Knowledge pipeline | 🟡 `ADR-021-gigaam-voice-pipeline.md` — модель GigaAM выбрана живым замером (e2e_rnnt), фаза 2 (схема/wiring/voice-Remember) в процессе. Ollama (1.8) — отдельно, не в этом ADR |
| 022 | Smart source dedup + versioning | ✅ `ADR-022-source-dedup-versioning.md` — дедуп сделан, версионирование сознательно отложено (решение владельца 31.08.2026, D2/§14.7) |
| 023 | Knowledge lifecycle management Panel/bot | ✅ `ADR-023-knowledge-lifecycle-panel-bot.md` — lifecycle памяти через бота сделан; документы через бота и любой lifecycle через Panel — нет |
| 024 | Scalable dynamic Knowledge taxonomy | ✅ `ADR-024-dynamic-domain-registry.md` — узкий срез, только реестр доменов, topics/aliases ждут Graphify |
| 025 | Global-within-user hybrid retrieval | ✅ `ADR-025-hybrid-retrieval.md` — модель выбрана по живому замеру (MiniLM-L12-v2, 384-dim), hybrid retrieval выкачен и подтверждён живьём 31.08.2026 |
| 026 | Safe ZIP batch ingest + exactly-once completion | ✅ `ADR-026-zip-batch-ingest.md` |
| 027 | Micro-Memory «Запомни» fast path | ✅ `ADR-027-micro-memory-fast-path.md` |
| 028 | Knowledge tenant model: SYSTEM_OWNER vs KNOWLEDGE_USER | ✅ `ADR-028-knowledge-tenant-model.md` |
| 029 | Dedicated Knowledge Telegram Bot + invite/principal verification | ✅ `ADR-029-dedicated-knowledge-telegram-bot.md` |
| 030 | Tenant isolation: user key + RLS + no cross-user dedup | ✅ `ADR-030-tenant-isolation-rls.md` |
| 031 | Per-user fair queue/quotas/style isolation | ✅ `ADR-031-per-user-fair-queue-quotas-style.md` |

**Написано 28 из 31, два (`019`, `021`) частично, два сознательно не
написаны.** Для оставшихся это не оговорка формата, а честный случай:
`002` и `007` — решение **действительно не принято** (n8n-адаптер и
переезд SignalAI, оба проверены отдельным исследованием 31.08.2026,
писать ADR не из чего). Ни один не «забыт» — для каждого выше в таблице
написано, почему именно он не написан.

## Наши собственные решения (100+)

| № | Тема |
|---|---|
| 101 | Часовой пояс сервера Europe/Moscow вместо Europe/Helsinki |
| 102 | Транспорт вложений Telegram/MAX: spool и двухшаговый диалог |
| 103 | Кэш ответов Knowledge не делается |
