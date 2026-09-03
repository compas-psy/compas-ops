#!/bin/bash
# HELM v4.0 RESCUE · R4.5.1 — распоряжение владельца 03.09.2026:
# доказать, какие именно safety-кейсы провалены всеми тремя моделями,
# прежде чем трогать golden fixtures или ослаблять gates.
#
# CaseScore хранит только счётчики совпадений (matched/extra), НЕ сам
# извлечённый контент — по канонit result.json нельзя увидеть, ЧТО
# именно модель придумала. Этот скрипт заново вызывает extract_window()
# на пяти помеченных кейсах для всех трёх моделей (5×3=15 вызовов,
# дёшево относительно полного 21-кейсового прогона) и печатает и
# сохраняет ПОЛНЫЙ extraction output — разрешено владельцем именно
# потому, что это synthetic golden-фикстуры без owner/health данных
# (вымышленные "Соколов Артём"/"Кузнецов Игорь" и т.п., уже открытым
# текстом лежащие в репозитории).
#
# Read-only относительно канонических результатов run 200 — ничего не
# перезаписывает, отдельный diagnostic artifact.
set -uo pipefail
cd /opt/helm/compose

DIAG_DIR=/opt/helm-state/benchmarks/r4/diagnostic-safety-cases
sudo mkdir -p "$DIAG_DIR"
sudo chown "$(id -u):$(id -g)" "$DIAG_DIR"

sudo docker compose exec -T helm-core python3 -c "
import dataclasses
import json

from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES
from helm_core.knowledge.semantic_benchmark_metrics import evaluate_case
from helm_core.knowledge.semantic_extract import extract_window

FLAGGED = (
    'no_knowledge',
    'provocative_no_fact_invention',
    'negative_statement',
    'date_unknown',
    'provocative_no_relation_invention',
)
MODELS = ('gemma2:2b', 'qwen2.5:3b', 'qwen2.5:7b')

cases = {c.case_id: c for c in GOLDEN_CASES if c.case_id in FLAGGED}
missing = set(FLAGGED) - set(cases)
if missing:
    raise SystemExit(f'кейсы не найдены в GOLDEN_CASES: {missing}')

full_output = {}
print('{:32} {:12} {:40} {}'.format('case_id', 'model', 'hallucination_type', 'notes'))
print('-' * 140)
for case_id in FLAGGED:
    case = cases[case_id]
    full_output[case_id] = {}
    for model in MODELS:
        extraction = extract_window(case.text, domain=case.domain,
                                    heading_path=case.heading_path,
                                    model=model, keep_alive='0')
        score = evaluate_case(case, extraction)

        hallucination_types = []
        if score.no_knowledge_violation:
            hallucination_types.append('no_knowledge_violation')
        if score.fabricated_dates:
            hallucination_types.append(f'fabricated_dates={score.fabricated_dates}')
        if score.fabricated_relations:
            hallucination_types.append(f'fabricated_relations={score.fabricated_relations}')
        if score.inverted_negations:
            hallucination_types.append(f'inverted_negations={score.inverted_negations}')
        if score.unsupported_fact_additions:
            hallucination_types.append(f'unsupported_fact_additions={score.unsupported_fact_additions}')
        type_str = ', '.join(hallucination_types) if hallucination_types else '(none)'
        notes_str = ' | '.join(score.notes) if score.notes else '(none)'
        print(f'{case_id:32} {model:12} {type_str:40} {notes_str}')

        full_output[case_id][model] = {
            'extraction': dataclasses.asdict(extraction),
            'score': dataclasses.asdict(score),
        }
    print()

with open('/tmp/r4_safety_diagnostic.json', 'w') as f:
    json.dump(full_output, f, ensure_ascii=False, indent=2)
print('полный extraction output сохранён в /tmp/r4_safety_diagnostic.json (внутри контейнера)')
"

sudo docker cp "$(sudo docker compose ps -q helm-core):/tmp/r4_safety_diagnostic.json" \
  "$DIAG_DIR/r4_safety_diagnostic.json"
echo
echo "############ Сохранено: $DIAG_DIR/r4_safety_diagnostic.json ############"
sudo wc -l "$DIAG_DIR/r4_safety_diagnostic.json"
