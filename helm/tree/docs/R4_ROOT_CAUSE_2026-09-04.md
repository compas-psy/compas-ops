# R4 Root Cause Analysis — run #241 (overall_pass=False), 2026-09-04

Владелец, после `R4 FINAL ACCEPTANCE — BLOCKED` (workflow run
[#241](https://github.com/compas-psy/compas-ops/actions/runs/33848827917),
`ddaa419be04f601106c68afc20a7f8c1b18d99a6`), поручил read-only root-cause
analysis по уже сохранённым артефактам, без нового inference/benchmark/deploy.
Это тот отчёт. Машиночитаемая версия — `docs/R4_FAILURE_MATRIX_2026-09-04.json`.

**Метод**: read-only recon (`scripts/r4-rca-dump.sh`, workflow run
[#242](https://github.com/compas-psy/compas-ops/actions/runs/33865271366)) —
только `cat` уже записанных `R4_FINAL_ACCEPTANCE.json`, `result.json`,
`fingerprint.json`, `resources-*.json`, `stderr.log` из
`/opt/helm-state/benchmarks/r4-final-acceptance/`. Ни один Ollama-вызов, ни
одна БД-запись, ни один рестарт. Все агрегаты ниже (relation_precision,
critical_entity_event_recall, processed_window_coverage) пересчитаны локально
из `result.json` и совпадают с `R4_FINAL_ACCEPTANCE.json` до 16 знака после
запятой — данные внутренне консистентны.

## 0. Что НЕ под вопросом

Все три структурные проверки прошли: `compiler_is_sole_edge_source` — OK,
`zero_cloud_relation_extraction` — OK, `non_vacuous_run` — OK
(cases_scored=20/21). R4.7-компилятор действительно единственный источник
рёбер в живом прогоне, и ни один вызов не ушёл за пределы локальной машины.
Все три реальных нарушения ниже — вопрос качества pass1-извлечения (и в одном
случае — методологии скоринга), а не того, что «скомпилировано не то».

Итог по гейтам, как он напечатан в `R4_FINAL_ACCEPTANCE.json`, содержит 4
строки, но по существу это **3 независимых нарушения**: `failed_cases = 1` и
`processed-window coverage = 95.2%` — одна и та же причина (единственный
упавший кейс), названная дважды двумя метриками.

## 1. Дисциплина: никаких новых live-запусков

Ни один Ollama-вызов не был сделан для этого отчёта. Ни модель, ни промпт, ни
пороги, ни компилятор, ни deploy не менялись. `ddaa419` остаётся
зафиксированным baseline с доказанным FAIL.

## 2. Coverage 95.2% — единственный упавший кейс

**`long_dense_window`** — самый плотный кейс золотого корпуса: 6 сущностей, 7
атомов, 9 рёбер, 9 категорий (`long_dense_window, multi_entity_atom,
typed_relations, date_day, date_month, decision_rationale, organization,
place, meeting_project_decision`) — единственный кейс с более чем 3
категориями.

| | |
|---|---|
| outcome | `failed` |
| error | `не удалось разобрать окно за 3 попыток: извлекатель недоступен: timed out` |
| repair_attempts | 3 (= `MAX_REPAIR_ATTEMPTS`, `semantic_extract.py:62`) |
| latency_seconds | 360.32 |
| stderr.log | пуст (0 байт) — сверх строки `error` дополнительной трассировки нет |

**Арифметика подтверждает диагноз без домыслов**: 360.32с ≈ 3 × 120с
(`REQUEST_TIMEOUT`, `semantic_extract.py:45`). Все 3 попытки индивидуально
упёрлись в HTTP-таймаут — ни одна не успела вернуть JSON целиком. Не JSON-парсинг,
не schema validation, не truncation — чистый transport timeout, воспроизводимая
арифметика, не гипотеза.

**Классификация: WINDOWING/RETRY.** Таймаут фиксирован (120с/попытка) и не
масштабируется по объёму ожидаемого ответа; это самое тяжёлое окно в корпусе.
Свойство harness-политики ретраев, не конкретной модели — любая модель
сопоставимой скорости упрётся в тот же потолок именно на этом окне.

**Важно и контринтуитивно**: `long_dense_window` содержит **6 critical
entities и 3 critical atoms** (`e1..e6` все critical; `a1, a5, a7` critical) —
больше critical-объектов, чем в любом другом кейсе корпуса. Но поскольку
`score=None` для failed-кейсов, `aggregate()` (`semantic_benchmark_metrics.py:499-503`)
**исключает** их из И числителя, И знаменателя `critical_entity_event_recall`.
Проверено программно: 27 critical gold / 23 matched — целиком из 20
успешно обработанных кейсов, ноль вклада от упавшего. **Это опровергает
исходную гипотезу «coverage failure тянет recall вниз» — на деле recall
проваливается независимо от coverage.** Более того: если coverage починить,
recall может даже УПАСТЬ — 9 новых critical items войдут в знаменатель, и
на самом плотном/сложном окне корпуса нет оснований ожидать образцового
извлечения.

## 3. Relation precision 70.0% — edge-by-edge

20 scored edges (proposed=28 → compiled=20 — компилятор уже отсеял 8
предложенных моделью рёбер как negroundable/wrong-family до скоринга).
14 typed-matched, 6 typed-extra (FP). Полная раскладка — `R4_FAILURE_MATRIX_2026-09-04.json`
(B5–B12); здесь — выводы.

**Главный вопрос владельца** («ошибочную семантику дал extractor или
корректные atoms испортил compiler?») имеет разный ответ для разных FP:

- **`place_event`, `concept_medical_specialty` — EXTRACTOR (HIGH confidence).**
  В обоих случаях сущность **сгруундирована** (`entities_matched=1`), но с
  **неверным `entity_type`** (`entity_type_correct: 0`). relation_compiler.py
  спроектирован как collision-free type-partition: каждый ENTITY type —
  легальная точка ровно одного auto-extractable-семейства. Неверный тип от
  extractor'а направляет верно найденную сущность в НЕ ТУ ветку компилятора —
  компилятор ведёт себя ровно так, как спроектирован, на некорректном входе.
  Это не баг компилятора.
- **`fact_plain` — EXTRACTOR (HIGH confidence).** `compiled_edges_count ==
  proposed_edges_count` ровно (1=1) — компилятор здесь чистый passthrough,
  ничего не добавил. Лишняя сущность и ребро — из pass1-галлюцинации на
  кейсе без единого golden-факта.
- **`typed_relations_variety`, `lecture_concept` — компилятор синтезировал
  БОЛЬШЕ рёбер, чем модель предложила** (`compiled > proposed`: 3>2 и 5>3
  соответственно) — минимум по одному лишнему ребру в каждом кейсе
  **compiler-attributable по построению** (компилятор не может добавить то,
  чего не было ни в proposed, ни выведено из entities/atoms им самим — а раз
  добавил, значит вывел из entities/atoms сверх того, что предложила модель).
  Оставшиеся FP в этих двух кейсах без сырых данных однозначно не
  атрибутируются.

**Один FP — не FP вовсе, а подтверждённый дефект эталона** (см. §5).

**Что нельзя сказать с уверенностью без новых данных**: конкретное
compiler-правило, сработавшее ошибочно в `typed_relations_variety` и
`lecture_concept`, и полная причина промаха в `provocative_no_fact_invention`
(обе конечные точки сматчены, ребро всё равно не выдано — см. B9 в матрице).
`GoldenBenchmarkReport` намеренно не хранит сырые `entities/atoms/edges`
(R4 п.2Б, то же архитектурное решение, что заставило строить AST-доказательства
для `r4_final_acceptance.py`) — здесь это та же самая архитектура впервые
мешает диагностике постфактум. См. §7.

## 4. Critical entity/event recall 85.2% — entity-by-entity

27 critical gold / 23 matched, **4 конкретных пропуска, все в 3 кейсах**
(проверено программно cross-reference с golden fixture, не оценка на глаз):

1. **`organization_fact`** — 1 из 2 critical entities не извлечена
   (`Иванова Мария` PERSON, `ООО «Ромашка»` ORGANIZATION — которая именно,
   не восстановить без сырых данных). **EXTRACTOR, MEDIUM confidence.**
2. **`date_year`** — И critical entity (`Казань`, PLACE), И critical atom
   полностью пропущены на коротком однофактном предложении; вместо них —
   посторонний неверно типизированный объект. **EXTRACTOR, HIGH confidence.**
3. **`same_label_different_entities`** — обе сущности-омонима «Иванов»
   корректно РАЗРЕШЕНЫ (`entities_matched=2/2` — дизамбигуация, проверенная
   офлайн ранее в этой сессии, на живом прогоне сработала). Пропущен только
   первый critical atom («Приём вёл терапевт Иванов.») — второе предложение
   («документы... подписал юрист Иванов») извлечено, первое — нет.
   **EXTRACTOR, HIGH confidence.**

Все три случая проверены против текста golden fixture (см. §5) — ожидание в
каждом обоснованно текстом, это не завышенные/некорректные gold-требования.

## 5. Санитарная проверка golden/evaluator

Проверил каждый mismatch на предмет ложного обвинения production-кода —
**не для того, чтобы подогнать эталон**, а чтобы не приписать extractor'у или
компилятору вину evaluator'а.

**Три пропуска recall (organization_fact, date_year, same_label_different_entities)
— эталон корректен**: в каждом случае ожидаемый объект прямо назван в тексте,
никаких натяжек.

**Один подтверждённый дефект найден** — не гипотеза, факт, проверенный по
исходному коду:

> `lecture_concept` golden fixture содержит ребро `e3 related_to e1`
> (гиперинфляция → инфляция). `RELATED_TO` **явно и сознательно исключён**
> из `AUTO_EXTRACTABLE_RELATIONS_V1` (`helm_core/models/base.py:379-384`,
> цитата из кода: *«RELATED_TO НЕ входит намеренно: владелец явно запретил
> его как fallback для сомнительной/неизвестной связи — "Неизвестный/
> сомнительный relation → NO EDGE", не понижение до RELATED_TO»*).
> Детерминированный компилятор **структурно не может** произвести
> `RELATED_TO` ни при каком качестве извлечения — это не пропуск, а
> гарантированный by-design NO-OP.

Просканировал программно весь корпус (37 gold edges, 21 кейс) на такие же
случаи — **это единственный** gold edge во всей корпусе вне
`AUTO_EXTRACTABLE_RELATIONS_V1`.

**Импакт, если исправить методологию** (не редактируя текст/сущности
эталона — только исключив нескомпилируемые relation_type из
`edges_gold_scoreable`): `relation_gold_scoreable` 22→21,
`relation_recall` 0.6364→0.6667 (**эта метрика не входит в перечень §14.18
нарушений** — она информационная). `relation_precision` (14/20=0.70) **не
меняется** — этот дефект не влияет на precision вообще, только на
некейтованный recall. **Ни одно из трёх реальных нарушений не переходит в
PASS от этого исправления.** Дефект реален и подтверждён, но не является
причиной текущего `R4=BLOCKED` — фиксирую это явно, чтобы не переоценить его
значимость.

**Классификация: EVALUATOR/FIXTURE.** Предлагаемое (не выполненное —
требует отдельного owner decision) исправление: не редактировать golden
fixture, а поправить `semantic_benchmark_metrics.py`, чтобы
`edges_gold_scoreable`/recall не засчитывали relation_type вне
`AUTO_EXTRACTABLE_RELATIONS_V1` для compiler-driven прогонов — метрика должна
измерять то, что компилятор способен произвести, а не весь исторический
LLM-ontology contract.

## 6. Итоговая классификация

| id | кейс | гейт | классификация | confidence |
|---|---|---|---|---|
| B1 | long_dense_window | coverage | WINDOWING/RETRY | HIGH |
| B2 | organization_fact | critical recall | EXTRACTOR | MEDIUM |
| B3 | date_year | critical recall | EXTRACTOR | HIGH |
| B4 | same_label_different_entities | critical recall | EXTRACTOR | HIGH |
| B5 | place_event | precision + recall | EXTRACTOR | HIGH |
| B6 | concept_medical_specialty | precision + recall | EXTRACTOR | HIGH |
| B7 | typed_relations_variety | precision + recall | AMBIGUOUS (≥1 edge compiler-attributable) | LOW |
| B8 | provocative_no_relation_invention | recall (не гейтится) | COMPILER (анафора вне scope label-matching) | MEDIUM |
| B9 | provocative_no_fact_invention | recall (не гейтится) | COMPILER | LOW |
| B10 | fact_plain | precision | EXTRACTOR | HIGH |
| B11 | lecture_concept (RELATED_TO) | precision + recall | EVALUATOR/FIXTURE | CONFIRMED |
| B12 | lecture_concept (лишние рёбра) | precision | COMPILER | MEDIUM |

Полные evidence/reasoning/proposed remediation по каждому пункту —
`R4_FAILURE_MATRIX_2026-09-04.json`. **Ни один пункт не предлагает
немедленное изменение кода** — это классификация и диагноз, действие ждёт
owner decision (мандат §8).

**Ответ на прямой вопрос владельца** («не делать вывод "qwen2.5:7b плохой"
по трём цифрам»): подтверждается. Из 3 нарушений — coverage целиком
инфраструктурная (harness timeout policy, не модель конкретно), 4 из 4
recall-промахов и минимум 3 из 6 precision-FP чётко EXTRACTOR/pass1-classified
(в т.ч. 2 — с чистым, повторяющимся паттерном «сматчено, но неверный
entity_type», что похоже на системную, а не случайную слабость pass1 на
редких/специфических типах, а не общую деградацию качества), но минимум 3
находки (B7 частично, B8, B9, B12) указывают на компилятор или на
неразрешённые архитектурные границы (анафора), и один — на дефект
эталона. Смешанная картина, не «модель плохая».

## 7. Чего не хватает в текущих артефактах

`GoldenBenchmarkReport`/`CaseRun` намеренно не хранит сырые
`entities/atoms/edges` (R4 п.2Б — та же гарантия, что уже потребовала
AST-доказательств для `r4_final_acceptance.py` вместо runtime-сравнения).
Это верно для zero-cloud/no-backfill инварианта, но означает: **edge-by-edge
и entity-by-entity атрибуция (B2, B3-частично, B7, B9, B12) невозможна
постфактум** — только агрегатные счётчики и точечные `notes` о полностью
пропущенных gold-рёбрах.

Новый live model run **не запускаю** для восполнения этого пробела (прямой
запрет мандата). Для СЛЕДУЮЩЕГО acceptance предлагаю (не делаю сейчас,
отдельное owner decision) privacy-safe диагностический слой: per-case JSON
`{entities, atoms, compiled_edges}` с `evidence_quote` (поле уже существует
на объектах в памяти — `semantic_extract.py:80,92,101`, не хватает только
persistence). Для текущего golden-корпуса риска приватности нет — это
синтетический текст, не пользовательские данные; для будущих R5+ прогонов
на реальных источниках такой слой потребует отдельную redaction-политику
(не относится к этому отчёту).

## 8. Дальше

Патч и regression tests — по мандату владельца, отдельным новым
commit/веткой от `ddaa419`, БЕЗ deploy и БЕЗ нового live acceptance до
следующего owner decision. `ddaa419` остаётся зафиксированным как
доказанный failed baseline. Этот отчёт и матрица — вход для этого решения,
не исполнение его.
