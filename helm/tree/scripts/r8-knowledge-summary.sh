#!/bin/bash
# HELM · наполнился ли Второй мозг знаниями после полного R8.
#
# action=recon: только читает. Исполнитель R7 закрывает транзакцию
# откатом, остальное — счётчики.
#
# Распоряжение владельца 05.09.2026: «дай мне простой человеческий отчёт
# всего по четырём числам… и покажи 3–5 примеров того, что реально
# извлеклось: врач → специальность → дата → источник».
#
# Четыре числа:
#   1. сколько источников обработано;
#   2. сколько доказанных врачей;
#   3. сколько из них с доказанной специальностью;
#   4. сколько событий получили доказанную дату.
#
# Числа 2 и 3 берутся не отдельным запросом, а из того же исполнителя,
# который отвечает живому пользователю: иначе отчёт мерил бы одно, а
# владелец видел бы другое.
#
# ИМЕНА И ФАЙЛЫ В ЖУРНАЛ НЕ ПОПАДАЮТ. Примеры печатаются с подписью
# «Врач A/B/C…» и коротким идентификатором источника; полный вид — с ФИО,
# цитатами и именами файлов — остаётся в файле 0600 на сервере, в том же
# периметре, где лежат сами документы (§5.2 CLAUDE.md).
set -uo pipefail
cd /opt/helm/compose || exit 1

DIR=/opt/helm/r7
OUT="$DIR/summary-$(date -u +%Y%m%dT%H%M%SZ).json"

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc "$1" 2>&1; }

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
sudo mkdir -p "$DIR"
sudo chmod 700 "$DIR"

echo "############ 1. ИСТОЧНИКИ ############"
echo "всего | с разбором semantic-v2 (обработано R8):"
psql "select count(*)||' | '||count(current_semantic_run_id) from knowledge_sources"
echo "по статусу разбора (статус | прогонов):"
psql "select status||' | '||count(*)::text from knowledge_semantic_runs
      group by status order by status"

echo "############ 4. ДАТЫ СОБЫТИЙ ############"
echo "health: событий | из них с датой:"
psql "select count(*)||' | '||count(occurred_at_start)
      from health.knowledge_nodes where kind='event'"
echo "public: событий | из них с датой:"
psql "select count(*)||' | '||count(occurred_at_start)
      from public.knowledge_nodes where kind='event'"
echo "health: узлов любого вида с датой | всего узлов:"
psql "select count(occurred_at_start)||' | '||count(*)::text from health.knowledge_nodes"

echo "############ 2-3. ВРАЧИ ГЛАЗАМИ ЖИВОГО ИСПОЛНИТЕЛЯ ############"
SUM=$(mktemp)
SUMTEXT=$(sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.query_router \
          --question 'каких врачей я посещал?' --out /tmp/r8-summary.json 2>&1)
RC=$?
printf '%s\n' "$SUMTEXT" > "$SUM"
if [ "$RC" -ne 0 ]; then
  echo "исполнитель вернул $RC:"
  tail -20 "$SUM"
  rm -f "$SUM"
  exit "$RC"
fi
python3 - "$SUM" <<'PYEOF'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)
print(f"доказанных врачей:            {data.get('items')}")
print(f"из них со специальностью:     {data.get('items_with_specialty')}")
print(f"из них с датой приёма:        {data.get('items_with_date')}")
print(f"доказательств всего:          {data.get('proofs_total')}")
print(f"пришло путём графа | цитат:   {data.get('items_from_graph')} | "
      f"{data.get('items_from_evidence')}")
print(f"личностей без врачебной роли: {data.get('uncovered_identities')}")
print(f"рёбер врачебной роли в графе: {data.get('graph_edges')}")
print("рассмотрено:", json.dumps(data.get("considered", {}), ensure_ascii=False))
print("отброшено:", json.dumps(data.get("skipped", {}), ensure_ascii=False))
PYEOF
rm -f "$SUM"

# Полный ответ на хост, 0600, и в лог не печатается ни разу.
sudo docker compose exec -T helm-core cat /tmp/r8-summary.json | sudo tee "$OUT" > /dev/null
sudo chmod 600 "$OUT"
sudo docker compose exec -T helm-core rm -f /tmp/r8-summary.json

echo "############ ПРИМЕРЫ ИЗВЛЕЧЁННОГО (обезличенно) ############"
echo "врач → специальность → дата → источник; ФИО и имена файлов — только в $OUT"
sudo cat "$OUT" | python3 -c '
import json
import sys

data = json.load(sys.stdin)
items = data.get("items", [])
if not items:
    print("  примеров нет: ни одного доказанного врача")
for i, item in enumerate(items[:5]):
    label = "Врач " + chr(ord("A") + i)
    spec = ", ".join(item.get("specialties") or []) or "специальность не подтверждена"
    dates = ", ".join(item.get("dates") or []) or "дата не подтверждена"
    proofs = item.get("proofs") or []
    src = proofs[0].get("source_id", "?")[:8] if proofs else "?"
    print(f"  {label} → {spec} → {dates} → источник #{src} ({len(proofs)} док-ва)")
if len(items) > 5:
    print(f"  … и ещё {len(items) - 5}")
'

echo "############ ФАЙЛ С ПОЛНЫМ ВИДОМ ############"
sudo stat -c '  %n | права %a | %U:%G | %s байт' "$OUT"
echo "############ ГОТОВО ############"
