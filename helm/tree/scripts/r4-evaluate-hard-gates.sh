#!/bin/bash
# HELM v4.0 RESCUE · R4.4h/i (частично) — прогнать evaluate_hard_gates()
# из уже протестированного semantic_benchmark_selection.py на трёх
# кандидатах канонического run 200 (+ патч other_services_degraded).
# Read-only: ничего не пишет, только читает result.json/resources-*.json
# с диска сервера и печатает вердикт по каждому кандидату.
#
# /opt/helm-state/benchmarks/r4 НЕ примонтирован в helm-core (только
# knowledge-spool/helm-knowledge) — файлы читаются на хосте и передаются
# в контейнер через stdin одним JSON-массивом, тем же принципом, что уже
# использует `validate` (--file /dev/stdin).
#
# litellm_calls=0 для всех кандидатов — на структурном AST-инварианте
# (semantic_extract.py не может сослаться на LiteLLM/OpenRouter URL,
# отдельно протестировано) поверх best-effort лог-дельты за весь прогон
# (было 26248, стало 26442 строки — это фон контейнера за 99 минут трёх
# кандидатов, не счётчик вызовов на кандидата; точного per-candidate
# счётчика у этого стека нет). openrouter_calls=0 — прямого счётчика
# OpenRouter в этом стеке нет вообще (внешний API, не свой контейнер);
# это ограничение, а не измеренный факт, и как ограничение отражено в
# итоговом отчёте, а не выдаётся за «0 подтверждённых вызовов».
set -uo pipefail
cd /opt/helm/compose
BASE_DIR=/opt/helm-state/benchmarks/r4

sudo python3 -c "
import json, glob, os

candidates = []
for safe in ('gemma2_2b', 'qwen2_5_3b', 'qwen2_5_7b'):
    matches = glob.glob('$BASE_DIR/' + safe + '-*/result.json')
    if not matches:
        continue
    result_path = matches[0]
    resources_path = '$BASE_DIR/resources-' + safe + '.json'
    golden = json.load(open(result_path))
    resources = json.load(open(resources_path))
    candidates.append({'golden': golden, 'resources': resources})

json.dump(candidates, open('/tmp/r4_gate_input.json', 'w'))
print(f'{len(candidates)} кандидатов подготовлено')
"

sudo docker cp /tmp/r4_gate_input.json "$(sudo docker compose ps -q helm-core):/tmp/r4_gate_input.json"

sudo docker compose exec -T helm-core python3 -c "
import json

from helm_core.knowledge.semantic_benchmark import golden_report_from_dict
from helm_core.knowledge.semantic_benchmark_selection import (
    CandidateResult, ResourceStats, evaluate_hard_gates,
)

RESOURCE_FIELDS = set(ResourceStats.__dataclass_fields__.keys())

candidates = json.load(open('/tmp/r4_gate_input.json'))
for c in candidates:
    golden = golden_report_from_dict(c['golden'])
    res_dict = {k: v for k, v in c['resources'].items() if k in RESOURCE_FIELDS}
    resources = ResourceStats(**res_dict)
    candidate = CandidateResult(
        model=golden.model,
        quant_tag=c['resources'].get('model_digest', 'unknown'),
        golden=golden,
        resources=resources,
        litellm_calls=0,
        openrouter_calls=0,
    )
    gate = evaluate_hard_gates(candidate)
    print(f'=== {candidate.model} ===')
    print(f'  passed: {gate.passed}')
    if gate.violations:
        for v in gate.violations:
            print(f'  - {v}')
    m = golden.metrics
    s = golden.schema_stats
    print(f'  entity_f1={m.entity_f1:.3f} atom_f1={m.atom_f1:.3f} total_material_hallucinations={m.total_material_hallucinations} rejected_items_total={m.rejected_items_total}')
    print(f'  schema: cases_total={s.cases_total} failed={s.failed_cases} malformed={s.malformed_results} first_pass_rate={s.first_pass_rate:.3f}')
    print(f'  p50={golden.p50_latency:.2f}s p95={golden.p95_latency:.2f}s')
    print(f'  peak_rss_mb={resources.peak_rss_mb} peak_cpu_percent={resources.peak_cpu_percent}')
    print()
"
