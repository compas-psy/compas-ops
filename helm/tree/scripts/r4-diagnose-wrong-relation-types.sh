#!/bin/bash
# HELM v4.0 RESCUE · R4.6.B follow-up (владелец 03.09.2026) — узкий
# прогон: r4-diagnose-relation-precision.sh нашёл 7/7 «wrong relation
# type» ошибок с extracted type='related_to' (валидатор §14.9
# нормализует неизвестный тип к RELATED_TO, а не отбрасывает связь —
# semantic_extract.py:422-424). Но сам этот скрипт печатал только
# rejected-строки с префиксом «связь» — сообщение «тип связи {X}
# сведён к related_to» начинается со слова «тип», под фильтр не
# попало и было потеряно (не в артефакте — в stdout того прогона).
# Без исходной строки {X} нельзя судить, синоним ли это (чинится
# реестром/промптом) или модель действительно перепутала связь.
#
# Только 4 кейса, где был замечен wrong_type: doctor_visit,
# multi_entity_atom, typed_relations_variety, purchase_warranty.
# Дублировать остальные 10 кейсов ради этого не нужно.
set -uo pipefail
cd /opt/helm/compose

PREEXISTING_MODELS=$(sudo docker compose exec -T ollama ollama list | tail -n +2 | awk '{print $1}')
OLLAMA_CID() { sudo docker compose ps -q ollama; }
ORIGINAL_MEM_LIMIT=$(sudo docker inspect -f '{{.HostConfig.Memory}}' "$(OLLAMA_CID)")
if [ "$ORIGINAL_MEM_LIMIT" = "0" ] || [ -z "$ORIGINAL_MEM_LIMIT" ]; then
  ORIGINAL_MEM_LIMIT_HUMAN="4g"
else
  ORIGINAL_MEM_LIMIT_HUMAN="${ORIGINAL_MEM_LIMIT}b"
fi
echo "=== временно поднимаем лимит ollama до 8g ==="
sudo docker update --memory=8g --memory-swap=8g "$(OLLAMA_CID)"
echo "=== ollama pull qwen2.5:7b ==="
sudo docker compose exec -T ollama ollama pull qwen2.5:7b

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES
from helm_core.knowledge.semantic_extract import ExtractionFailed, WindowTruncated, extract_window

TARGET = ("doctor_visit", "multi_entity_atom", "typed_relations_variety", "purchase_warranty")
cases = {c.case_id: c for c in GOLDEN_CASES if c.case_id in TARGET}

for case_id in TARGET:
    case = cases[case_id]
    print(f"########## {case_id} ##########")
    print(f"  gold edges: {[(e.from_ref, e.relation_type, e.to_ref) for e in case.edges]}")
    try:
        extraction = extract_window(case.text, domain=case.domain, heading_path=case.heading_path,
                                    model="qwen2.5:7b", keep_alive="0")
    except (WindowTruncated, ExtractionFailed) as exc:
        print(f"  ПРОПУЩЕН: {exc}")
        continue
    print(f"  extracted edges: {[(e.from_local_id, e.relation_type, e.to_local_id) for e in extraction.edges]}")
    print(f"  rejected (ВСЕ, без фильтра):")
    for r in extraction.rejected:
        print(f"    - {r}")
    print()
PYEOF
diag_rc=$?

echo "############ ВОССТАНОВЛЕНИЕ ИСХОДНОГО СОСТОЯНИЯ OLLAMA ############"
current_models=$(sudo docker compose exec -T ollama ollama list | tail -n +2 | awk '{print $1}')
for m in $current_models; do
  found=0
  for p in $PREEXISTING_MODELS; do
    [ "$m" = "$p" ] && found=1 && break
  done
  if [ "$found" -eq 0 ]; then
    sudo docker compose exec -T ollama ollama rm "$m" >/dev/null 2>&1 || true
  fi
done
sudo docker update --memory="$ORIGINAL_MEM_LIMIT_HUMAN" --memory-swap="$ORIGINAL_MEM_LIMIT_HUMAN" "$(OLLAMA_CID)"

if [ "$diag_rc" -ne 0 ]; then
  echo "::error::диагностика завершилась с кодом $diag_rc"
  exit "$diag_rc"
fi
