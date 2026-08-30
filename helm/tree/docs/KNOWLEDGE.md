# HELM Knowledge — обзор (ТЗ §14, v3.4)

Второй мозг владельца: долговременная память вне LLM-чата. Задача —
сохранять источники, находить в них ответ бесплатно и детерминированно
раньше платной модели, и передавать в облако только минимальный evidence
pack, когда локального ответа объективно недостаточно.

Это НЕ ещё один AI-чат и НЕ обязательная graph DB — Neo4j не используется,
документы не переписываются в сотни «atomic notes», вывод модели никогда
не считается фактом сам по себе.

## Статус на 29.08.2026

| Часть | Статус |
|---|---|
| P8.5.1 Storage (схема БД, каталоги, backup) | ✅ задеплоено, подтверждено живьём |
| P8.5.4/5 частично — лексический Probe (Z0/Z1/NEEDS_REASONING), wiring в `/hooks/max` и `helm-control` | ✅ задеплоено, подтверждено живьём (см. `docs/KNOWLEDGE_RETRIEVAL.md`) |
| P8.5.2 Parsers/attachment path — parser router + async worker (job queue, quality gate, эскалация) | ✅ задеплоено, подтверждено живьём: `smoke-test.docx` → `markitdown`/`done`, `smoke-test-broken.pdf` → эскалация на `docling`/`needs_review` (см. `docs/KNOWLEDGE_INGEST.md`) |
| P8.5.3 GigaAM (ASR) | ❌ не реализовано |
| P8.5.4 остаток — pg_trgm, dense/embeddings, pgvector, rank fusion | ❌ не реализовано |
| P8.5.5 остаток — Z2 (опциональный локальный генератор) | ❌ не реализовано (спекой разрешено оставить выключенным) |
| P8.5.6 Graphify challenger | ❌ не реализовано |
| P8.5.7 Telegram/MAX ingress вложений (spool → RAW → register_file_for_ingest, двухшаговый диалог домена) | ✅ ОБА канала задеплоены и подтверждены живьём (реальные PDF, все 3 шага диалога включая уведомление о завершении разбора, `ADR-021`). По пути найдены и исправлены живые баги: cross-device rename, отсутствие `TelegramSender` в outbox, блокировка исходящего трафика к Telegram на уровне сети/`ufw` — заменяет прежнее agentic-чтение файлов чифом, не сосуществует с ним (решение владельца) |
| P8.5.2.1 ZIP batch ingest (v3.7) — safe expansion + durable child queue + exactly-once финал | ⚠️ код готов, 250/250 тестов зелёных, **ждёт живого деплоя** (`ADR-024`, `V3.7-DELTA.md`) — не проверено ни на реальном Telegram, ни на MAX |
| P8.5.8 Panel строка Knowledge | ❌ не реализовано (бессмысленно без данных) |
| v3.8 P8.6.1 схема тенантности + P8.6.3 PostgreSQL RLS | ⚠️ код готов, миграции `ef1ba5467e14`/`4da8c9e90115`, **ждёт живого деплоя** (`V3.8-DELTA.md`) |
| v3.8 P8.5.12 Micro-Memory «Запомни» (text, без голоса/reply-to) | ⚠️ код готов, подключено в MAX/Telegram (owner) и Dedicated Knowledge Bot (secondary), **ждёт живого деплоя** (`V3.8-DELTA.md`) — голос (GigaAM) и «Запомни это» как ответ на сообщение не реализованы |
| v3.8 P8.6.2 Dedicated Telegram Knowledge Bot + onboarding | ⚠️ код готов, `/hooks/knowledge-telegram` + one-use invite, **ждёт живого деплоя** (`ADR-025`) — владелец ещё не завёл `KNOWLEDGE_TELEGRAM_BOT_TOKEN` через BotFather; файлы/ZIP/голос для secondary-пользователей не реализованы |
| v3.8 P8.6.4 per-user quotas + fair queue | ⚠️ код готов, `knowledge/quotas.py` (storage/daily-ingest байты, глубина очереди) + round-robin по тенантам в `claim_next_job()`, **ждёт живого деплоя** (`V3.8-DELTA.md`) — квота не декрементируется при archive/disable, редактирование квот через Panel не реализовано |
| v3.8 P8.6.7 offboarding | 🟡 частично: suspend/reactivate (`knowledge/onboarding.py` + internal API) готовы, **ждут живого деплоя**; vault-экспорт и RED физическое удаление аккаунта не реализованы |

Подробности реализации — в `docs/KNOWLEDGE_INGEST.md` (что и как попадает
в базу) и `docs/KNOWLEDGE_RETRIEVAL.md` (что и как из неё достаётся).
Статус выбора моделей (embeddings/GigaAM/Z2) — `docs/KNOWLEDGE_MODELS.md`.
Вложения Telegram/MAX (P8.5.7): двухшаговый диалог выбора домена, MAX-
транспорт, открытый вопрос по Telegram-транспорту — `docs/adr/ADR-021-
knowledge-attachment-transport.md`.
Методология перехода на v3.4 и список того, что осталось —
`implementation-state/V3.4-DELTA.md`. v3.8 (Micro-Memory + multi-user
tenancy): `implementation-state/V3.8-DELTA.md`. Dedicated Knowledge
Telegram Bot + onboarding (P8.6.2): `docs/adr/ADR-025-dedicated-
knowledge-telegram-bot.md`.

## Четыре уровня памяти (§14.1)

```text
L0 RAW        оригинальный файл/аудио/текст, immutable, SHA256
L1 SOURCE     детерминированная/ASR-конверсия в читаемый Markdown
L2 KNOWLEDGE  долговечные concept/entity/meeting/decision notes
L3 INFERENCE  выводы/гипотезы — никогда не становятся FACT автоматически
```

При конфликте приоритет: `RAW/live primary source > SOURCE extraction >
owner-approved KNOWLEDGE > derived KNOWLEDGE > INFERENCE`.

Путь L0→L1 реализован двумя способами: `ingest_text()` (готовый текст,
НЕ пишет файлы на диск — только `knowledge_sources`+`knowledge_chunks`)
и `register_file_for_ingest()` + `worker.py` (реальный файл на диске →
async parse → реальный L1 SOURCE `.md`-файл + chunks, см.
`docs/KNOWLEDGE_INGEST.md`). L2 knowledge notes (consolidation) не
создаются ни там, ни там — спека explicitly разрешает ingest'у
остановиться на `RAW + SOURCE + chunks + indexes` без LLM-вызова.

## Каталоги (§14.2)

```text
/opt/helm-knowledge/
├── inbox/
├── raw/{personal,health,simpas/company,simpas/practice,simpas/zapiski,
│        simpas/moments,psy-marketing,ventures,engineering,signalai-docs,
│        library}
├── sources/  concepts/  entities/  meetings/  decisions/  projects/
├── research/  archive/
└── derived/graphify/
```

Создано и живёт на сервере (`scripts/knowledge-bootstrap.sh`, idempotent).
`/opt/helm-knowledge` никогда не коммитится в Forgejo/GitHub — это
runtime/private data под ACL и encrypted restic backup, не код.
`raw/` immutable по контракту спеки: сегодня туда ничего физически не
пишется (см. выше), контракт соблюдён тривиально до P8.5.2.

## Namespaces и ACL (§14.15)

Закрытый список `KnowledgeDomain` (`helm_core/models/base.py`):

```text
personal, health, simpas/company, simpas/practice, simpas/zapiski,
simpas/moments, psy-marketing, ventures, engineering, signalai-docs,
library
```

`library` добавлен решением владельца 29.08.2026 (внешняя справочная
литература — книги по психологии для загрузки и поиска — отдельно от
`simpas/practice`, который про рабочую практику самого SIMPAS).

`health` по спеке требует отдельную схему/роль Postgres и reviewer
temporary explicit scope — **не реализовано**, сейчас только логическая
изоляция: `probe()` без явного `domain="health"` исключает домен `health`
из общего поиска (`KnowledgeSource.domain.notin_([HEALTH, SIMPAS_ZAPISKI])`).
Отдельная схема/роль для `health` — самостоятельная задача, не блокирует
остальной P8.5 (см. «Conflicts found» в V3.4-DELTA.md).

`ЗАПИСКИ` (`simpas/zapiski`, клиентский контент) — по спеке `NEVER
AUTO-INGEST`. P8.5.7 (двухшаговый диалог вложений) реализует явный guard:
владелец, выбравший этот домен для конкретного файла, — это и есть
допустимое "владелец вручную отправил" (§14.15), `chat_intake.py`
принудительно ставит `sensitivity=client_restricted` независимо от
диалога.

**Важная находка (аудит 29.08.2026, а не то, что подразумевает
формулировка выше)**: "явный scope открывает доступ" технически верно
на уровне Python (`probe(session, query=..., domain="health")` реально
работает и протестирован), но **ни один живой канал сегодня не вызывает
probe() с явным domain вообще** — ни `/hooks/max`, ни `helm-control`
(Telegram) никогда не передают `domain`, только `query`. Значит файл,
загруженный в `health` ИЛИ в `simpas/zapiski` через P8.5.7, успешно
попадает в базу, но **не находится обычным вопросом в чате** — ни
бесплатным Probe (исключён из общего поиска намеренно), ни платным
Hermes/chief (тот вообще не видит базу знаний, только текст вопроса).
Владелец, спросивший о таком файле обычным сообщением, получит либо
"не знаю" от chief, либо галлюцинацию — не тихий сбой, а видимый, но
это стоит знать заранее, а не как сюрприз. Механизм вызова explicit
scope из чата (аналог, например, домен-алиасов из P8.5.7 — команда вида
`health: <вопрос>`) — отдельная, не начатая задача.

## Что дальше

`implementation-state/WORKPLAN.md`, раздел «HELM Knowledge (P8.5)» —
актуальный план оставшихся подфаз и их зависимостей.
