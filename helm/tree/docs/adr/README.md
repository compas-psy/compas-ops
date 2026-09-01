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
| 001 | Hermes cognitive / Control Plane canonical state | не написан |
| 002 | n8n adapter boundary | не написан |
| 003 | LiteLLM routing ownership | не написан |
| 004 | Hermes state/Kanban non-canonical | не написан |
| 005 | Health isolation | не написан |
| 006 | Forgejo primary / GitHub mirror+CI | не написан |
| 007 | SignalAI single-writer migration | не написан |
| 008 | Skills promotion | не написан |
| 009 | Action registry / RED executor | ✅ `ADR-009-action-registry.md` |
| 010 | Graduated trust | не написан |
| 011 | No heavy monitoring stack in v1 | не написан |
| 012 | Durable execution DBOS spike decision | ✅ `ADR-012-durable-execution.md` |
| 013 | Telegram pre-dispatch Control Plane gate | не написан |
| 014 | MAX direct Control Plane ingress | ✅ `ADR-014-max-responses-api.md` |
| 015 | HELM Panel static frontend + Control Plane backend | не написан |
| 016 | Panel Telegram OIDC + WebAuthn | не написан |
| 017 | Live-server-first initial deployment | ✅ `ADR-017-offline-build.md` — отклонено, выбрана офлайн-сборка |
| 018 | Multimodel implementation policy | ✅ `ADR-018-single-agent.md` — отклонено фактически |
| 019 | Knowledge canonical Markdown/Postgres; Graphify derived | не написан |
| 020 | Strict zero-paid Knowledge lock | не написан |
| 021 | Local parser/GigaAM/Ollama Knowledge pipeline | 🟡 `ADR-021-gigaam-voice-pipeline.md` — модель GigaAM выбрана живым замером (e2e_rnnt), фаза 2 (схема/wiring/voice-Remember) в процессе. Ollama (1.8) — отдельно, не в этом ADR |
| 022 | Smart source dedup + versioning | не написан |
| 023 | Knowledge lifecycle management Panel/bot | не написан |
| 024 | Scalable dynamic Knowledge taxonomy | ✅ `ADR-024-dynamic-domain-registry.md` — узкий срез, только реестр доменов, topics/aliases ждут Graphify |
| 025 | Global-within-user hybrid retrieval | ✅ `ADR-025-hybrid-retrieval.md` — модель выбрана по живому замеру (MiniLM-L12-v2, 384-dim), hybrid retrieval выкачен и подтверждён живьём 31.08.2026 |
| 026 | Safe ZIP batch ingest + exactly-once completion | ✅ `ADR-026-zip-batch-ingest.md` |
| 027 | Micro-Memory «Запомни» fast path | ✅ `ADR-027-micro-memory-fast-path.md` |
| 028 | Knowledge tenant model: SYSTEM_OWNER vs KNOWLEDGE_USER | ✅ `ADR-028-knowledge-tenant-model.md` |
| 029 | Dedicated Knowledge Telegram Bot + invite/principal verification | ✅ `ADR-029-dedicated-knowledge-telegram-bot.md` |
| 030 | Tenant isolation: user key + RLS + no cross-user dedup | ✅ `ADR-030-tenant-isolation-rls.md` |
| 031 | Per-user fair queue/quotas/style isolation | ✅ `ADR-031-per-user-fair-queue-quotas-style.md` |

**Написано 13 из 31.** Важная оговорка: «не написан» не значит «решение
не принято». Часть оставшихся решений уже реализована в коде и
обоснована в `V3.8-DELTA.md` и `WORKPLAN.md` — не хватает именно
отдельного документа в формате ADR. Список §35 требует их до передачи.

## Наши собственные решения (100+)

| № | Тема |
|---|---|
| 101 | Часовой пояс сервера Europe/Moscow вместо Europe/Helsinki |
| 102 | Транспорт вложений Telegram/MAX: spool и двухшаговый диалог |
| 103 | Кэш ответов Knowledge не делается |
