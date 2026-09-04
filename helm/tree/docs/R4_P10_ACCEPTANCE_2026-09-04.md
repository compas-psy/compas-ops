# R4 P10 — final acceptance run (commit 7c5fc97): overall_pass = False

Владелец: см. "OWNER DECISION — R4 FINAL REMEDIATION" (P1–P10, 2026-09-04).
Этот документ закрывает P10: "затем ОДИН новый full live R4 acceptance...
если FAIL: R4 STOP, дать owner точные raw mismatches и архитектурную
развилку." Ровно один live-прогон израсходован (run #247, GitHub Actions
run id 33903773733). Артефакты сняты read-only через `r4-rca-dump.sh`
(run #248, id 33910320976) — без рестартов, без новых inference-вызовов.

**Результат: R4 STOP. Код не менялся, новый прогон не запускался.**

## 0. Идентичность и целостность прогона

- `run_id`: `r4final-20260904T180258Z`, `git_sha`: `7c5fc9730f6894e4baa192cbd398661955f3874d`
  (полное SHA256-совпадение по всем 81 файлам `helm_core/**/*.py` подтверждено
  до этого прогона — см. коммит `7aa49fc`'s parent state).
- `model`: `qwen2.5:7b`, `model_digest`: `845dbda0ea48` — тот же digest, что и
  во всех предыдущих R4-прогонах. Модель не менялась.
- Lifecycle/health чист: `snapshot_restore.restored_ok=true`,
  `health_check.before/after` — `helm_core: OK`, `postgres: OK` до и после,
  `oom_occurred=false`, `other_services_degraded=false`.
- `schema_stats`: `cases_total=21, failed_cases=0, truncated_cases=0,
  first_pass_rate=1.0, processed_window_coverage=1.0` — **coverage 100%**,
  никаких timeout/repair-failures. `long_dense_window` (единственный кейс,
  падавший таймаутом в run 241 — 3×120с identical retry) в этом прогоне
  корректно разбит P2-механизмом на 3 куска (`p0`, `p1`, `p2` в
  `raw_diagnostics.json`) и обработан без единой ошибки.
- Структурные инварианты: `compiler_is_sole_edge_source=true`,
  `zero_cloud_relation_extraction=true`, `non_vacuous_run=true`.
- `relation_precision = 1.0` (было ~0.70 до ремедиации) — P3–P6 сработали
  на precision-сторону без регрессии.

Это подтверждает: **P2 (timeout→split) и P4–P6 (compiler/evaluator fixes)
работают корректно и без сюрпризов.** Проблема — не в них.

## 1. Вердикт

```
overall_pass: False
hard_gate_passed: False
  VIOLATION: critical expected entity/event recall = 58.3% (требуется >= 90%)
  VIOLATION: identifier corruption on exact fixture = 1
```

`critical_entity_event_recall = 58.3%` — **хуже**, чем базовый показатель до
ремедиации (85.2%, RCA `f7c0ade`, §4). Это не "модель всё ещё недостаточно
хороша" — это регрессия, внесённая в этом прогоне. Ниже — точная причина,
установленная по коду и по `raw_diagnostics.json`/`result.json`, не
предположением.

## 2. Корневая причина recall-регрессии: ложное "дублирование local_id"

### 2.1 Симптом

`atoms_matched = 0` в 19 из 21 кейсов, при этом `entities`
извлекаются и матчатся почти без потерь (`critical_entities_matched`
практически везде равен `critical_entities_gold`). Пример
(`R4_FINAL_ACCEPTANCE.json.metrics`): `atom_recall = 0.125`,
`entity_recall = 0.885`. Потери сосредоточены **только на слое atoms**.

По кейсам (`result.json.runs[].score`, `raw_diagnostics.json`):

| case_id | atoms_gold | atoms_matched | rejected | rejected-сообщение |
|---|---|---|---|---|
| doctor_visit | 1 | 0 | 1 | `повтор local_id '1'` |
| organization_fact | 2 | 0 | 1 | `повтор local_id '1'` |
| place_event | 1 | 0 | 1 | `повтор local_id '1'` |
| fact_plain | 1 | 0 | 1 | `повтор local_id '1'` |
| event_month | 1 | 0 | 1 | `повтор local_id '1'` |
| decision_rationale | 2 | 0 | 1 | `повтор local_id '1'` |
| date_year | 1 | 0 | 1 | `повтор local_id '1'` |
| date_unknown | 1 | 0 | 1 | `повтор local_id '1'` |
| same_label_different_entities | 2 | 0 | 2 | `повтор local_id '1'`, `'2'` |
| negative_statement | 2 | 0 | 1 | `повтор local_id '1'` |
| ambiguous_text | 1 | 0 | 1 | `повтор local_id '1'` |
| **long_dense_window** | 7 | **0** | 6 | `'1'×2, '2', '3', '4', '1'` |
| provocative_no_relation_invention | 3 | 1 | 2 | `повтор local_id '1'`, `'2'` |
| provocative_no_fact_invention | 1 | 0 | 1 | `повтор local_id '1'` |
| purchase_warranty | 3 | 0 | 2 | `повтор local_id '1'`, `'2'` |
| lecture_concept | 2 | 0 | 2 | `повтор local_id '1'`, `'2'` |
| multi_entity_atom | 3 | 3 | 0 | — (единственный полностью чистый кейс) |

`rejected_items_total = 29` (`R4_FINAL_ACCEPTANCE.json`), почти все —
`повтор local_id`.

### 2.2 Механизм (код, не гипотеза)

`extract_nodes_window()` → `_extract_nodes_once()` →
`return validate(raw, window_text=window_text)`
(`semantic_extract.py:673`). `_extract_nodes_once` — новая функция этой
сессии (P1/P2), но парсинг она делегирует **старой** `validate()`
(строки 446–651), написанной для схемы с `edges`.

`validate()` использует **один общий** `known: set[str]` на entities
И atoms вместе (строки 472, 484–485, 515–516):

```python
known: set[str] = set()
for item in data.get("entities") or []:
    ...
    if local_id in known:
        result.rejected.append(f"повтор local_id {local_id!r}")
        continue
    ...
    known.add(local_id)
for item in data.get("atoms") or []:
    ...
    if local_id in known:              # <- та же общая известь
        result.rejected.append(f"повтор local_id {local_id!r}")
        continue
    ...
```

Это **корректно** для старой схемы (`extract_window()`, edges): рёбра
адресуют source/target по `local_id`, который может указывать и на
entity, и на atom — поэтому общее пространство id было обязательным
контрактом, и старый `NODE_SYSTEM_PROMPT`-эквивалент (строки 271, 350)
явно требовал у модели "local_id уникальны внутри этого ответа" единым
счётчиком.

В node-only-пути (P1) `NODE_SYSTEM_PROMPT` **не содержит** этого
требования (подтверждено P9-тестом "prompt never mentions
edges/relations" — и в нём действительно нет фразы про общий счётчик).
Модель, как и любая обычная LLM без явной инструкции, нумерует
`entities` и `atoms` **независимыми** счётчиками 1, 2, 3... в одном
JSON-ответе. Первый атом почти всегда получает `local_id="1"` —
и коллидирует с первой сущностью, у которой тоже `local_id="1"`.
`validate()` реагирует так, как её и написали: молча выбрасывает
атом целиком как "дубликат".

Downstream-код не нуждается в общем пространстве id для node-only
пути: `ExtractedAtom` не хранит ссылок на entity по `local_id`
(грепом по `models/base.py`/`semantic_extract.py` подтверждено — только
`ExtractedEdge.from_local_id/to_local_id` читают `local_id`
кросс-типово, а edges в этой схеме структурно не существуют).
`_merge_node_extractions()` при делении окна (P2) переименовывает
`local_id` префиксом куска равномерно для entities и atoms — ей тоже
всё равно, пересекались ли номера внутри куска.

**Вывод: общий `known`-namespace в `validate()` — осознанный контракт
старой edge-схемы, ошибочно унаследованный `_extract_nodes_once()` для
схемы, где этого контракта больше нет и неоткуда взяться (ни в промпте,
ни в даунстриме). Это регрессия, внесённая мной в P1/P2 этой сессии, а
не слабость `qwen2.5:7b` и не факт о золотом корпусе.**

### 2.3 Что это НЕ объясняет

`entity_precision = 0.639` (ниже, чем можно было ожидать) — открытый
вопрос, не обязан быть тем же багом; отдельный анализ не проводился
(вне рамок read-only recon этого прогона, требует офлайн-разбора
`raw_diagnostics.json` по каждой лишней сущности — не сделан, чтобы не
выходить за read-only диагностику до решения владельца).

## 3. Второе нарушение: identifier corruption (не связано с §2)

`aliases` case, `result.json.runs[].score.notes`:

```
e1: identifier corruption — gold='Сбербанк' extracted='ПАО Сбербанк'
```

Это единственная запись `exact_identifier_corruptions=1` во всём
прогоне. Сущность **найдена и заматчена** (`critical_entities_matched=1`
для этого кейса) — расхождение только в буквальном значении label:
модель извлекла полное юридическое название вместо краткого golden-label.
Это НЕ проявление бага §2 (там объекты теряются целиком, а не
матчатся-с-искажением) — отдельная, более мелкая находка. Открытый
вопрос владельцу: является ли "ПАО Сбербанк" при golden="Сбербанк"
действительно порчей идентификатора, или это over-strict fixture
(модель дала более полную, а не искажённую форму) — сам по себе не
подтверждён и не отвергнут в рамках read-only recon.

## 4. Побочная находка (harness, не блокирует)

В `/opt/helm-state/benchmarks/r4-final-acceptance/` остался каталог
`qwen2_5_7b-4dce51227d8e1871` (38005 байт `result.json`) — след
предыдущего прогона под другим fingerprint, не относящийся к этому
акцепту. Каталог не бинд-моунтится и не чистится между прогонами
(известно с прошлой RCA). Не влияет на вердикт этого прогона —
`r4-rca-dump.sh` корректно выбрал актуальный каталог
(`qwen2_5_7b-e076787b3297c316`, сортировка `tail -1` по алфавиту дала
верный результат: `e...` > `4...`). Упомянуто для честности данных
(§5.1), не как new finding, требующий действия сейчас.

## 5. Архитектурная развилка (решение владельца, не выполнено)

**(a) Минимальная хирургическая правка (рекомендация).** Дать
node-only-парсингу два независимых `known`-множества — одно для
entities, одно для atoms — вместо одного общего. Ничего в даунстриме
node-only-пути не требует кросс-типовой уникальности (edges
структурно отсутствуют). Не трогает `validate()`/`extract_window()`
для старого edge-пути — либо новая тонкая функция парсинга, либо
параметр в `validate()`, переключающий поведение. Ожидаемый эффект:
устраняет 29 ложных rejection, вероятно возвращает `atom_recall`
и `critical_entity_event_recall` к порядку величины 85%+ (пре-ремедиационный
baseline) или выше — **точное число не установлено без нового прогона**,
которого мандат P10 не разрешает без решения владельца.

**(b) Альтернатива — заставить модель шарить один счётчик.** Добавить
в `NODE_SYSTEM_PROMPT` явное требование "local_id уникален по всему
ответу, entities и atoms — один счётчик" (как в старом промпте).
Не рекомендую: борется с естественным поведением модели вместо того,
чтобы принять его; более хрупко (зависит от того, действительно ли
модель будет соблюдать инструкцию на каждом вызове), тогда как (a) —
структурная гарантия, не зависящая от послушности модели.

**Открыто независимо от выбора (a)/(b):** математическая заметка
владельца (P8) о том, что даже идеальный `long_dense_window` даёт
recall не выше ~88.9% — а значит фикс §2 сам по себе может быть
**необходимым, но не факт, что достаточным** условием для гейта 90%.
Этот прогон не даёт данных, чтобы подтвердить или опровергнуть
достаточность — баг §2 подавляет вообще весь сигнал по atom-recall,
включая те самые организационно-специфичные промахи
(`organization_fact`, `date_year`, `same_label_different_entities`),
которые P8 отдельно называл требующими настоящего фикса, а не
fixture-хаков.

## 6. Статус по мандату P10

R4 STOP. Не выполнено и не будет выполнено без нового решения
владельца: код не менялся (кроме read-only `r4-rca-dump.sh`, коммит
`7aa49fc` — уже запушен, до получения этого вердикта), новый
live-прогон не запускался, R4.8/R4.9/F3/эксперименты с моделью не
начинались, гейты не ослаблялись. `7c5fc97` — новая frozen failed
baseline поверх `ddaa419`, ожидает решения владельца по §5 (a) или (b).
