#!/bin/bash
# HELM · ЧЕТЫРЕ live-вызова на РЕАЛЬНОМ production-пути. Не больше.
#
# Распоряжение владельца 05.09.2026: «После deploy — ровно 4 live-вызова.
# Не больше… После этого никаких пятого, десятого и двадцатого canary
# "для уверенности".»
#
#   1. «Каких врачей я посещал?»                — HARD GATE подключения
#   2. «Что сказал врач на приёме?»             — ловушка ложного intent
#   3. «Каких врачей я посещал в 2014 году?»    — заведомо отсутствующий год
#   4. «Каких врачей я посещал в этом году?»    — TEMPORAL COVERAGE GATE
#
# Четвёртый — не гейт подключения. При occurred_at_start = 0/388
# (замер 297) правильный результат: router отработал, ни один
# недатированный визит НЕ приписан 2026 году, и об этих визитах сказано
# отдельно. Это PASS подключения и DEBT покрытия по времени, а не провал
# роутера.
#
# Вопрос идёт не в CLI, а в тот же /internal/knowledge/probe, который
# дёргает плагин helm-control на каждое сообщение владельца в Telegram,
# с той же подписью. Режим S1 — доказательство, что детерминированный
# router отработал внутри живого запроса: старый код такого значения
# вернуть не мог.
#
# Наружу — outcome, режим, длины, флаги, вердикты. Текст ответа —
# медицинские данные владельца: он уходит в файл 0600 и не появляется в
# журнале GitHub Actions ни при каком исходе (§5.2). Секрет читается на
# сервере и не печатается (§5.4).
set -uo pipefail

CP=http://127.0.0.1:8080
OUT=/opt/helm-knowledge-private/forensics
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
SHA=$(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)
STATE=$(mktemp -d)
trap 'rm -rf "$STATE"' EXIT

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc "$1" 2>&1; }

echo "выкачено: $SHA"
sudo install -d -m 0700 -o helm -g helm "$OUT"

echo "############ ЖУРНАЛ ДО ############"
echo "строк knowledge_answer_runs | из них S1:"
psql "select count(*)||' | '||count(*) filter (where mode='S1') from knowledge_answer_runs"

SECRET=$(sudo cat /etc/helm/secrets/hermes_service_hmac)

ask() {
  local tag="$1" question="$2" body ts sig resp code rc
  body=$(python3 -c 'import json,sys; print(json.dumps({"query": sys.argv[1]}, ensure_ascii=False))' "$question")
  ts=$(date +%s.%N)
  sig=$(printf '%s\0%s' "$ts" "$body" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')
  resp=$(mktemp)
  code=$(curl -sS -o "$resp" -w '%{http_code}' -X POST "$CP/internal/knowledge/probe" \
         -H "Content-Type: application/json" \
         -H "X-Helm-Timestamp: $ts" \
         -H "X-Helm-Signature: $sig" \
         --data-binary "$body")
  echo "код ответа: $code"
  python3 - "$resp" "$tag" "$STATE" <<'PYEOF'
import json
import re
import sys
from pathlib import Path

path, tag, state = sys.argv[1], sys.argv[2], Path(sys.argv[3])
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)

text = data.get("answer_text") or ""
lines = [ln for ln in text.splitlines() if ln.strip()]
head = lines[0] if lines else ""
low = text.lower()

banned = ("документ", "чанк", "фрагмент", "уверенност", "граф", "evidence",
          "возможно", "вероятно", "похоже", "найдено")
acceptance_banned = ("исследован", "диагноз", "заключен", "анализ крови")
specialty_head = bool(re.fullmatch(r"[А-ЯЁ][а-яё-]+(, [а-яё-]+)*\.", head))
names = re.findall(r"([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ]\.){0,2})\s+—", text)
refused = "Не нашёл в ваших данных" in text
year_named = bool(re.search(r"за (19|20)\d{2} год", text))
undated_reported = bool(re.search(r"\d+ врач(?:|а|ей), дату приёма", text))

local = data.get("outcome") == "LOCAL_ANSWER"
s1 = data.get("mode") == "S1"
short = len(lines) <= 4
clean = not any(w in low for w in banned) and not any(w in low for w in acceptance_banned)
uniq = len(names) == len(set(names))

print(f"outcome: {data.get('outcome')}")
print(f"mode: {data.get('mode')}")
print(f"строк: {len(lines)} | символов: {len(text)}")
print(f"первая строка — перечень специальностей: {specialty_head}")
print(f"имён в ответе: {len(names)} | уникальных: {len(set(names))}")
print(f"честный отказ: {refused} | год назван: {year_named}")
print(f"недатированные названы числом: {undated_reported}")
print(f"запрещённые слова: {[w for w in banned if w in low]}")
print(f"перечисление исследований/диагнозов: {[w for w in acceptance_banned if w in low]}")

if tag == "C1":
    (state / "c1_names").write_text(str(len(names)))
    gates = {
        "локальный бесплатный ответ, не Z1/C1/платная модель": local,
        "режим S1 — QueryRouter отработал": s1,
        "врачи возвращены, а не отказ": not refused and bool(names or specialty_head),
        "специальности впереди": specialty_head,
        "каноническая личность не продублирована": uniq,
        "не длиннее четырёх строк": short,
        "нет запрещённых слов и перечислений": clean,
    }
elif tag == "C2":
    gates = {
        "НЕ классифицирован как запрос списка врачей": data.get("mode") != "S1",
        "перечня специальностей нет": not specialty_head,
    }
elif tag == "C3":
    gates = {
        "режим S1 — намерение распознано": s1,
        "остался в структурном пути, платная модель не вызвана": local,
        "сказано, что подтверждённых посещений за год нет": refused,
        "год назван явно": year_named,
        "ни один недатированный визит не приписан 2014": len(names) == 0,
        "не длиннее четырёх строк": short,
    }
else:  # C4 — покрытие по времени, не подключение
    c1_names = int((state / "c1_names").read_text()) if (state / "c1_names").exists() else 0
    gates = {
        "режим S1 — QueryRouter отработал": s1,
        "платная модель не вызвана": local,
        "ни один недатированный визит не приписан 2026": len(names) == 0,
        "сказано, что подтверждённых визитов за год нет": refused,
        "год назван явно": year_named,
        # Требуется только если врачи в данных вообще есть — это показал C1.
        "недатированные визиты названы отдельно": undated_reported or c1_names == 0,
        "не длиннее четырёх строк": short,
    }

for name, ok in gates.items():
    print(("ПРОШЁЛ  " if ok else "ПРОВАЛ  ") + name)
print("ИТОГ: " + ("PASS" if all(gates.values()) else "FAIL"))
sys.exit(0 if all(gates.values()) else 1)
PYEOF
  rc=$?
  sudo install -m 0600 -o helm -g helm "$resp" "$OUT/canary-$tag-$STAMP.json"
  shred -u "$resp" 2>/dev/null || rm -f "$resp"
  return $rc
}

echo "############ 1 · HARD GATE подключения · «Каких врачей я посещал?» ############"
ask C1 "Каких врачей я посещал?"; RC1=$?

echo "############ 2 · ловушка ложного intent · «Что сказал врач на приёме?» ############"
ask C2 "Что сказал врач на приёме?"; RC2=$?

echo "############ 3 · отсутствующий год · «Каких врачей я посещал в 2014 году?» ############"
ask C3 "Каких врачей я посещал в 2014 году?"; RC3=$?

echo "############ 4 · покрытие по времени · «Каких врачей я посещал в этом году?» ############"
ask C4 "Каких врачей я посещал в этом году?"; RC4=$?

unset SECRET

echo "############ ЖУРНАЛ ПОСЛЕ ############"
echo "строк knowledge_answer_runs | из них S1:"
psql "select count(*)||' | '||count(*) filter (where mode='S1') from knowledge_answer_runs"
echo "последние 4 строки (время | режим | evidence | платно):"
psql "select to_char(created_at,'HH24:MI:SS')||' | '||mode||' | '||evidence_count::text||' | '
      ||paid_ai_used::text from knowledge_answer_runs order by created_at desc limit 4"

echo "############ ФАЙЛЫ С ПОЛНЫМИ ОТВЕТАМИ ############"
sudo find "$OUT" -name "canary-*-$STAMP.json" -printf '%f | %m | %s\n' | sort | sed 's/^/  /'

echo "############ ИТОГ ############"
echo "выкачено: $SHA"
echo "1 подключение (без года): $([ $RC1 -eq 0 ] && echo PASS || echo FAIL)"
echo "2 ловушка intent:        $([ $RC2 -eq 0 ] && echo PASS || echo FAIL)"
echo "3 отсутствующий год:     $([ $RC3 -eq 0 ] && echo PASS || echo FAIL)"
echo "4 покрытие по времени:   $([ $RC4 -eq 0 ] && echo PASS || echo FAIL)"
if [ $RC1 -eq 0 ] && [ $RC2 -eq 0 ] && [ $RC3 -eq 0 ] && [ $RC4 -eq 0 ]; then
  echo "PRODUCTION WIRING ACCEPTED · покрытие по времени остаётся долгом R4/R4.5"
  exit 0
fi
echo "ЧЕТЫРЕ ВЫЗОВА НЕ ДАЛИ ОЖИДАЕМОГО ПОВЕДЕНИЯ"
exit 1
