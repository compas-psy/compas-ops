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
# ФИО, ИМЕНА ФАЙЛОВ И ЦИТАТЫ ПЕЧАТАЮТСЯ В ЖУРНАЛ — прямые решения
# владельца от 05.09.2026: «имена и файлы печатай прямо в отчёт, это мои
# данные» и «цитаты тоже показывай». Данные принадлежат ему, он
# распорядился ими явно; сужать это решение за него агент не вправе.
#
# Цитата — это ТОЧНЫЙ спан из источника (R5), то есть короткий кусок
# вокруг подписи, а не окно и не абзац. Печатается не более трёх на
# врача и не длиннее 300 символов: отчёт должен читаться, а не быть
# выгрузкой. Файл 0600 по-прежнему пишется и содержит всё целиком.
#
# Обе границы урезания названы вслух, иначе отчёт врёт умолчанием:
# «показаны 3 из N доказательств», а у обрезанной цитаты диапазон
# помечен «полный спан», потому что char_start/char_end описывают
# исходный спан целиком, а не те 300 символов, что видны в отчёте.
#
# Имя файла health-источника лежит не в public.knowledge_sources, а в
# health.knowledge_source_private: это единственное реально
# чувствительное поле health-конверта, и оно вынесено в приватную схему
# ещё в R1. Без объединения обеих таблиц у health-примеров источник был
# бы пустым.
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

echo "############ 1b. ДОЛГ ПОКРЫТИЯ ############"
# Обещано владельцу 05.09.2026: источники, прочитанные не целиком,
# называются числом, а не умалчиваются. В порции 303 один источник
# закрылся degraded с покрытием 0.708 — «обработано» и «прочитано
# полностью» это разные вещи, и §14.19 требует их различать.
echo "текущие ревизии (статус | источников | окон | из них упало | среднее покрытие):"
psql "select r.status||' | '||count(*)::text||' | '||sum(r.windows_total)::text||' | '||
             sum(r.windows_failed)::text||' | '||coalesce(round(avg(r.coverage_ratio),3),0)::text
      from knowledge_semantic_runs r
      join knowledge_sources s on s.current_semantic_run_id = r.id
      group by r.status order by count(*) desc"
echo "прочитанные не целиком (файл | покрытие | окон упало из скольких):"
psql "select coalesce(hp.original_filename, s.original_filename, '(без имени)')||' | '||
             coalesce(r.coverage_ratio,0)::text||' | '||
             r.windows_failed::text||' из '||r.windows_total::text
      from knowledge_semantic_runs r
      join knowledge_sources s on s.current_semantic_run_id = r.id
      left join health.knowledge_source_private hp on hp.source_id = s.id
      where coalesce(r.coverage_ratio,1) < 1 or r.windows_failed > 0
      order by r.coverage_ratio limit 10"

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

# Полный ответ (с цитатами) — на хост, 0600. Копия в temp нужна затем,
# что разбор ниже читает её обычным пользователем, а не через sudo.
ANS=$(mktemp)
sudo docker compose exec -T helm-core cat /tmp/r8-summary.json | tee "$ANS" > /dev/null
sudo install -m 0600 -o helm -g helm "$ANS" "$OUT"
sudo docker compose exec -T helm-core rm -f /tmp/r8-summary.json

echo "############ ПРИМЕРЫ ИЗВЛЕЧЁННОГО ############"
echo "врач → специальность → дата → источник"
NAMES=$(mktemp)
psql "select id::text||E'\t'||coalesce(original_filename,'(без имени)')
      from public.knowledge_sources
      union all
      select source_id::text||E'\t'||coalesce(original_filename,'(без имени)')
      from health.knowledge_source_private" > "$NAMES"
python3 - "$ANS" "$NAMES" <<'PYEOF'
import json
import sys

files = {}
with open(sys.argv[2], encoding="utf-8") as fh:
    for row in fh:
        if "\t" in row:
            key, _, value = row.strip().partition("\t")
            # health-имя перекрывает пустой конверт из public: приватная
            # строка и есть настоящее имя файла.
            if value and value != "(без имени)" or key not in files:
                files[key] = value

def proofs_word(count):
    tail_two, tail_one = count % 100, count % 10
    if 11 <= tail_two <= 14 or tail_one == 0 or tail_one >= 5:
        return "доказательств"
    return "доказательство" if tail_one == 1 else "доказательства"


with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)
items = data.get("items", [])
if not items:
    print("  примеров нет: ни одного доказанного врача")
for item in items[:5]:
    spec = ", ".join(item.get("specialties") or []) or "специальность не подтверждена"
    dates = ", ".join(item.get("dates") or []) or "дата не подтверждена"
    proofs = item.get("proofs") or []
    src = files.get(proofs[0].get("source_id", ""), "источник не найден") if proofs else "-"
    shown = min(len(proofs), 3)
    tail = f", показаны {shown} из {len(proofs)}" if len(proofs) > 3 else ""
    print(f"  {item.get('person')} → {spec} → {dates} → {src} "
          f"({len(proofs)} {proofs_word(len(proofs))}{tail})")
    for proof in proofs[:3]:
        quote = (proof.get("quote") or "").strip()
        if quote:
            # Границы спана относятся к ПОЛНОЙ цитате. Если её обрезали,
            # печатать их как «символы X–Y» значило бы выдать границы
            # обрезка за границы показанного — поэтому у обрезанной
            # цитаты они прямо названы полным спаном.
            cut = len(quote) > 300
            if cut:
                quote = quote[:300] + "…"
            where = files.get(proof.get("source_id", ""), "источник не найден")
            span = ""
            if proof.get("char_start") is not None:
                label = "полный спан: символы" if cut else "символы"
                span = f", {label} {proof['char_start']}–{proof['char_end']}"
            print(f"      «{quote}» — {where}{span}")
        elif proof.get("edge_id"):
            # Путь графа: доказательство — само ребро, точных границ у
            # него нет (см. докстринг Proof). Подставлять сюда окно
            # значило бы выдать приблизительное место за точное.
            print(f"      ребро графа {proof['edge_id'][:8]} — "
                  f"{files.get(proof.get('source_id', ''), 'источник не найден')}")
if len(items) > 5:
    print(f"  … и ещё {len(items) - 5}")
PYEOF
shred -u "$ANS" "$NAMES" 2>/dev/null || rm -f "$ANS" "$NAMES"

echo "############ ФАЙЛ С ПОЛНЫМ ВИДОМ ############"
sudo stat -c '  %n | права %a | %U:%G | %s байт' "$OUT"
echo "############ ГОТОВО ############"
