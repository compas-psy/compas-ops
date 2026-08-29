# HELM Knowledge — Retrieval / Free-first Probe (ТЗ §14.9–§14.14)

Как вопрос ищет ответ в базе знаний до платной модели, что из спеки уже
работает и что нет. Подтверждено живьём 29.08.2026 на реальном сервере.

## Что есть сегодня: только лексический слой

`helm_core/knowledge/probe.py::probe(session, *, query, domain=None)`.

Спека (§14.9, §14.11) описывает три независимых сигнала объединённых
rank fusion — lexical (FTS), dense (embeddings), relations
(`knowledge_relations`) — плюс optional local rerank. Реализован только
**lexical**. Dense и rank fusion — P8.5.4 остаток, требует бенчмарка
embedding-модели на живом сервере (см. `docs/KNOWLEDGE_MODELS.md`).
Relations не участвуют — `knowledge_relations` пока пустая таблица,
ничто не создаёт в неё записи (extraction связей — P8.5.2+).

Практическое следствие: probe находит меньше, чем финальная версия
(семантическая перефразировка без общих слов с источником не найдётся),
но то, что находит — находит бесплатно, детерминированно и с
проверяемым provenance.

### Lexical search — `_lexical_search()`

```text
plainto_tsquery('russian', query)
→ OR-ификация: replace(...::text, ' & ', ' | ')::tsquery
→ ts_rank(chunk.tsv, tsquery, normalization=2)
→ фильтр по domain (явный domain, либо все кроме health)
→ фильтр status != ARCHIVED
→ ORDER BY rank DESC LIMIT 5
```

Два реальных бага PostgreSQL FTS найдены живыми тестами (§30.8.5 golden
cases написаны ПЕРВЫМИ) и подтверждены напрямую в `psql` до правки кода:

1. **`plainto_tsquery` AND-комбинирует все стеммы запроса.** Вопрос
   «какое решение приняли» → `'как' & 'решен' & 'приня'` — требует ВСЕХ
   трёх корней в документе одновременно. Реалистичный документ-факт
   («Решение: используем Postgres.») содержит только один общий корень
   с вопросом — `@@` честно возвращал false, а не «слабое совпадение».
   Исправлено OR-ификацией: любой корень запроса достаточен для
   попадания в кандидаты, `ts_rank` по-прежнему ранжирует документы с
   бОльшим числом совпавших термов выше.
2. **`ts_rank` без `normalization` игнорирует длину документа.** Длинный
   нерелевантный документ с одним случайным совпадением получал тот же
   ранг, что короткий релевантный (~0.0203 у обоих в тестовом случае).
   Исправлено `normalization=2` (делит на длину документа) — создаёт
   чистое ~7-кратное разделение между шумом (~0.0009) и реальными
   совпадениями (0.0068–0.0203).

`MIN_RANK_SCORE = 0.003` — порог между шумом и сигналом, откалиброван
эмпирически на этих числах. Это первая прикидка, не финальная
калибровка (§14.13 сама говорит «calibrated threshold» — калибровать
не на чем: реального golden-набора с большим корпусом ещё нет).

### Answer modes — Z0 / Z1 / NEEDS_REASONING

```text
1 совпадение выше порога  → Z0, extractive: текст чанка + "Источник: ..."
≥2 совпадений выше порога → Z1, детерминированный нумерованный список
0 совпадений выше порога  → NEEDS_REASONING
```

Оба режима — без LLM (`_compose_answer()`, чистый Python). `Z2`
(опциональный локальный генератор) не реализован — спека явно разрешает
оставить его выключенным («Z2 is not allowed to delay B.5 completion»);
Z0/Z1 уже дают бесплатные ответы, C1 защищает качество там, где их
недостаточно.

## Wiring — обе точки входа до Hermes

`POST /internal/knowledge/probe` (HMAC service auth, `helm_core/api/
internal.py`) — единственный вызов `probe()` извне модуля.

**MAX** (`helm_core/api/hooks.py::max_webhook`) — вызывает `probe()`
in-process (Control Plane сам обслуживает и вебхук, и Probe, никакого
HTTP-перехода). `LOCAL_ANSWER` → ответ в outbox напрямую, `chief`
(Hermes) не вызывается вовсе. `NEEDS_REASONING` → обычный путь к Hermes
через фоновую задачу; после реального ответа Hermes логируется
`knowledge_answer_runs` (`mode=C1, paid_ai_used=true`) — единственное
место, где эта постфактум-метрика §14.14 реализована.

**Telegram** (`hermes/plugins/helm-control/__init__.py`) — вызывает тот
же эндпоинт по HTTP (HMAC-подписанный запрос, тот же паттерн, что
регистрация задачи, но **fail-open**: недоступность Probe не блокирует
сообщение, оно просто идёт к LLM как обычно без бесплатного пути в этот
раз — в отличие от регистрации задачи, которая fail-closed по
конструкции). `LOCAL_ANSWER` отправляется напрямую через
`TelegramAdapter.send(chat_id=..., content=...)`, chief не вызывается.

**Известный пробел (F-260829-25, открыт)**: для Telegram
`NEEDS_REASONING → реальный ответ Hermes` НЕ логирует `C1` постфактум —
Control Plane не получает от Hermes gateway событие о завершении хода
(в отличие от MAX, где сам делает HTTP-вызов и видит ответ). Сам
Probe-гейт при этом работает одинаково на обоих каналах; недостаёт
только доли метрики §14.14 для платных Telegram-эскалаций.

### Health ACL (§14.15)

`domain=None` (обычный вопрос) исключает `KnowledgeDomain.HEALTH` из
поиска на уровне SQL-запроса — health не течёт в общий ответ по
умолчанию. Явный `domain="health"` — доступ есть, отдельный путь, не
общий поиск.

## Quality gate §14.13 — что выполняется, что нет

| Критерий | Статус |
|---|---|
| ACL PASS | ✅ (health exclusion) |
| top evidence score >= calibrated threshold | ⚠️ порог есть, калибровка первая прикидка, не финальная |
| source provenance present | ✅ (`original_filename`/`source_id` в каждом ответе) |
| no unresolved contradiction | ❌ не реализовано — требует `knowledge_relations`, которая пуста (extraction связей — P8.5.2+); известный, задокументированный пробел, не молчаливый |
| coverage sufficient | ⚠️ только лексическое покрытие, без dense/relations |
| answer claims traceable to evidence | ✅ (Z0/Z1 — чистая экстракция, не синтез) |

## Тесты

`tests/test_knowledge_probe.py` (13), `tests/test_api.py` (эндпоинт),
`tests/test_max_channel.py` (webhook-интеграция) — 138/138 зелёных.
Полный набор golden cases §30.8.5 (semantic paraphrase, multi-hop
relation, contradictory sources, Graphify-сравнение) недостижим без
dense retrieval/relations — появится вместе с P8.5.4 остатком/P8.5.6.
