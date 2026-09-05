#!/bin/bash
# HELM · три live-canary на РЕАЛЬНОМ production-пути.
#
# Распоряжение владельца 05.09.2026: «после завершения deploy делаем
# РОВНО ТРИ live-canary через настоящий production /internal/knowledge/
# probe. Никакой новой тестовой матрицы, если эти три проходят.»
#
# Вопрос идёт не в CLI, а в тот же эндпоинт, который дёргает плагин
# helm-control на каждое сообщение владельца в Telegram, с той же
# подписью. Режим S1 в ответе — доказательство, что детерминированный
# router отработал внутри живого запроса: старый код такого значения
# вернуть не мог.
#
# Четвёртый вызов — не четвёртый canary, а диагностика к первому: тот же
# вопрос без года. Он нужен ровно затем, чтобы отличить «сломан
# структурный путь» от «в графе нет ни одной даты» (замер 297: 0 дат из
# 388 узлов). Без него провал canary 1 не имел бы причины.
#
# Наружу — outcome, режим, длины, флаги и вердикты гейтов. Текст ответа
# — медицинские данные владельца: он уходит в файл 0600 и не появляется
# в журнале GitHub Actions ни при каком исходе (§5.2). Секрет читается
# на сервере и не печатается (§5.4).
set -uo pipefail

CP=http://127.0.0.1:8080
OUT=/opt/helm-knowledge-private/forensics
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
SHA=$(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc "$1" 2>&1; }

echo "выкачено: $SHA"
sudo install -d -m 0700 -o helm -g helm "$OUT"

echo "############ ЖУРНАЛ ДО ############"
echo "строк knowledge_answer_runs | из них S1:"
psql "select count(*)||' | '||count(*) filter (where mode='S1') from knowledge_answer_runs"

SECRET=$(sudo cat /etc/helm/secrets/hermes_service_hmac)

ask() {
  local tag="$1" question="$2" body ts sig resp code
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
  python3 - "$resp" "$tag" <<'PYEOF'
import json
import re
import sys

path, tag = sys.argv[1], sys.argv[2]
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
# ФИО в поимённой строке: «Фамилия И. О. — …». Дубль канонического врача
# виден как повтор одного и того же имени в одной строке.
names = re.findall(r"([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ]\.){0,2})\s+—", text)

print(f"outcome: {data.get('outcome')}")
print(f"mode: {data.get('mode')}")
print(f"строк: {len(lines)} | символов: {len(text)}")
print(f"первая строка — перечень специальностей: {specialty_head}")
print(f"первая строка без инициалов: {bool(head) and not re.search(r'[А-ЯЁ]\\.', head)}")
print(f"имён в поимённой части: {len(names)} | уникальных: {len(set(names))}")
print(f"сказал «не нашёл подтверждённого»: {'Не нашёл в ваших данных' in text}")
print(f"назвал год в отказе: {bool(re.search(r'за (19|20)[0-9]{2} год', text))}")
print(f"запрещённые слова: {[w for w in banned if w in low]}")
print(f"перечисление исследований/диагнозов: {[w for w in acceptance_banned if w in low]}")

local = data.get("outcome") == "LOCAL_ANSWER"
s1 = data.get("mode") == "S1"
short = len(lines) <= 4
clean = not any(w in low for w in banned) and not any(w in low for w in acceptance_banned)
uniq = len(names) == len(set(names))

if tag == "C1":
    gates = {"локальный бесплатный ответ": local,
             "режим S1 — router отработал": s1,
             "первая строка — специальности": specialty_head,
             "канонический врач не продублирован": uniq,
             "не длиннее четырёх строк": short,
             "нет запрещённых слов и перечислений": clean}
elif tag == "C1b":
    gates = {"локальный бесплатный ответ": local,
             "режим S1 — router отработал": s1,
             "первая строка — специальности": specialty_head,
             "канонический врач не продублирован": uniq,
             "не длиннее четырёх строк": short,
             "нет запрещённых слов и перечислений": clean}
elif tag == "C2":
    gates = {"НЕ ушёл в структурный путь": data.get("mode") != "S1",
             "не выдал перечень специальностей": not specialty_head}
else:  # C3
    gates = {"режим S1 — намерение распознано": s1,
             "не ушёл в платную модель": local,
             "честный отказ": "Не нашёл в ваших данных" in text,
             "год назван в отказе": bool(re.search(r"за (19|20)[0-9]{2} год", text)),
             "не длиннее четырёх строк": short,
             "ни одного имени врача": len(names) == 0}

for name, ok in gates.items():
    print(("ПРОШЁЛ  " if ok else "ПРОВАЛ  ") + name)
print("ИТОГ: " + ("PASS" if all(gates.values()) else "FAIL"))
sys.exit(0 if all(gates.values()) else 1)
PYEOF
  local rc=$?
  sudo install -m 0600 -o helm -g helm "$resp" "$OUT/canary-$tag-$STAMP.json"
  shred -u "$resp" 2>/dev/null || rm -f "$resp"
  return $rc
}

echo "############ CANARY 1 · «Каких врачей я посещал в этом году?» ############"
ask C1 "Каких врачей я посещал в этом году?"; RC1=$?

echo "############ ДИАГНОСТИКА К 1 · тот же вопрос без года ############"
echo "нужна, чтобы отличить сломанный структурный путь от отсутствия дат в графе"
ask C1b "Каких врачей я посещал?"; RC1B=$?

echo "############ CANARY 2 · «Что сказал врач на приёме?» ############"
ask C2 "Что сказал врач на приёме?"; RC2=$?

echo "############ CANARY 3 · «Каких врачей я посещал в 2014 году?» ############"
ask C3 "Каких врачей я посещал в 2014 году?"; RC3=$?

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
echo "canary 1  (год): $([ $RC1 -eq 0 ] && echo PASS || echo FAIL)"
echo "диагностика (без года): $([ $RC1B -eq 0 ] && echo PASS || echo FAIL)"
echo "canary 2  (ловушка): $([ $RC2 -eq 0 ] && echo PASS || echo FAIL)"
echo "canary 3  (2014): $([ $RC3 -eq 0 ] && echo PASS || echo FAIL)"
if [ $RC1 -eq 0 ] && [ $RC2 -eq 0 ] && [ $RC3 -eq 0 ]; then
  echo "ТРИ CANARY ПРОЙДЕНЫ"
  exit 0
fi
echo "ТРИ CANARY НЕ ПРОЙДЕНЫ"
exit 1
