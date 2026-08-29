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
| P8.5.7 Telegram/MAX ingress вложений (spool → RAW → register_file_for_ingest) | ❌ не реализовано (async-парсинг, к которому оно ведёт, — уже готов, P8.5.2 выше) |
| P8.5.8 Panel строка Knowledge | ❌ не реализовано (бессмысленно без данных) |

Подробности реализации — в `docs/KNOWLEDGE_INGEST.md` (что и как попадает
в базу) и `docs/KNOWLEDGE_RETRIEVAL.md` (что и как из неё достаётся).
Статус выбора моделей (embeddings/GigaAM/Z2) — `docs/KNOWLEDGE_MODELS.md`.
Методология перехода на v3.4 и список того, что осталось —
`implementation-state/V3.4-DELTA.md`.

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
из общего поиска (`KnowledgeSource.domain != KnowledgeDomain.HEALTH`),
явный scope открывает доступ. Отдельная схема/роль — самостоятельная
задача, не блокирует остальной P8.5 (см. «Conflicts found» в
V3.4-DELTA.md).

`ЗАПИСКИ` (`simpas/zapiski`, клиентский контент) — по спеке `NEVER
AUTO-INGEST`. Сегодня автоматического ingest вообще нет ни для одного
домена (только ручной `ingest_text()` без транспорта), так что это
правило не нарушено тривиально; при появлении P8.5.7 (Telegram/MAX
ingress) явный guard на этот домен обязателен — зафиксировать при
реализации.

## Что дальше

`implementation-state/WORKPLAN.md`, раздел «HELM Knowledge (P8.5)» —
актуальный план оставшихся подфаз и их зависимостей.
