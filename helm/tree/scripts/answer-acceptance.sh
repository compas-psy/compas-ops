#!/bin/bash
# HELM · приёмка контракта ответа на РЕАЛЬНОМ production-пути.
#
# Распоряжение владельца 05.09.2026: «Нужно доказать, что production
# действительно использует новый query-router, а не обходит его через
# старый generic RAG/LLM path», и HARD GATE: «Каких врачей я посещал в
# этом году?» обязан вернуть СПЕЦИАЛИЗАЦИИ первым делом.
#
# Поэтому вопрос идёт не в CLI, а в тот же самый эндпоинт
# /internal/knowledge/probe, который дёргает плагин helm-control на
# каждое сообщение владельца в Telegram. Подпись считается так же.
#
# Наружу — outcome, режим и структурные признаки ответа. Сам текст
# ответа — медицинские данные владельца: он уходит в файл 0600 и не
# появляется в журнале GitHub Actions ни при каком исходе (§5.2).
#
# Секрет читается на сервере и не печатается: агент секретов не видит
# (§5.4).
set -uo pipefail

CP=http://127.0.0.1:8080
QUESTION="Каких врачей я посещал в этом году?"
OUT=/opt/helm-knowledge-private/forensics
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc "$1" 2>&1; }

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
echo "вопрос: $QUESTION"

echo "############ 1. ЖУРНАЛ ДО ЗАПРОСА ############"
echo "строк knowledge_answer_runs всего | из них S1:"
psql "select count(*)||' | '||count(*) filter (where mode='S1') from knowledge_answer_runs"

echo "############ 2. ЗАПРОС В ТОТ ЖЕ ЭНДПОИНТ, ЧТО У ЖИВОГО БОТА ############"
SECRET=$(sudo cat /etc/helm/secrets/hermes_service_hmac)
BODY=$(python3 -c 'import json,sys; print(json.dumps({"query": sys.argv[1]}, ensure_ascii=False))' "$QUESTION")
TS=$(date +%s.%N)
SIG=$(printf '%s\0%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')
unset SECRET
RESP=$(mktemp)
CODE=$(curl -sS -o "$RESP" -w '%{http_code}' -X POST "$CP/internal/knowledge/probe" \
       -H "Content-Type: application/json" \
       -H "X-Helm-Timestamp: $TS" \
       -H "X-Helm-Signature: $SIG" \
       --data-binary "$BODY")
echo "код ответа: $CODE"

echo "############ 3. СТРУКТУРА ОТВЕТА (без текста) ############"
python3 - "$RESP" <<'PYEOF'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)

text = data.get("answer_text") or ""
lines = [line for line in text.splitlines() if line.strip()]
head = lines[0] if lines else ""

# Запрещено контрактом владельца по умолчанию.
banned = ("документ", "чанк", "фрагмент", "уверенност", "граф", "evidence",
          "возможно", "вероятно", "похоже", "найдено")
# Провал приёмки отдельно назван: перечисление исследований и диагнозов
# вместо ответа про специальности.
acceptance_banned = ("исследован", "диагноз", "заключен", "анализ крови")

print("outcome:", data.get("outcome"))
print("mode:", data.get("mode"))
print("строк в ответе:", len(lines))
print("символов в ответе:", len(text))
print("первая строка — только слова без инициалов:",
      bool(head) and " — " not in head and not re.search(r"\b[А-ЯЁ]\.", head))
print("первая строка похожа на перечень специальностей:",
      bool(re.fullmatch(r"[А-ЯЁ][а-яё-]+(, [а-яё-]+)*\.", head)))
print("сказал «не нашёл подтверждённого»:",
      text.startswith("Не нашёл в ваших данных подтверждённого ответа."))
low = text.lower()
print("запрещённых слов контракта:", [w for w in banned if w in low])
print("перечисления исследований/диагнозов:",
      [w for w in acceptance_banned if w in low])

gates = {
    "ответ бесплатный и локальный": data.get("outcome") == "LOCAL_ANSWER",
    "режим S1 — сработал детерминированный router": data.get("mode") == "S1",
    "не длиннее четырёх строк": len(lines) <= 4,
    "нет запрещённых слов": not any(w in low for w in banned),
    "нет перечисления исследований и диагнозов":
        not any(w in low for w in acceptance_banned),
}
print("############ ГЕЙТЫ ############")
for name, ok in gates.items():
    print(("ПРОШЁЛ " if ok else "ПРОВАЛ ") + name)
sys.exit(0 if all(gates.values()) else 1)
PYEOF
GATES=$?

echo "############ 4. ЖУРНАЛ ПОСЛЕ ЗАПРОСА ############"
echo "строк knowledge_answer_runs всего | из них S1:"
psql "select count(*)||' | '||count(*) filter (where mode='S1') from knowledge_answer_runs"
echo "последняя строка (время | режим | evidence | платно):"
psql "select to_char(created_at,'YYYY-MM-DD HH24:MI:SS')||' | '||mode||' | '
      ||evidence_count::text||' | '||paid_ai_used::text
      from knowledge_answer_runs order by created_at desc limit 1"

echo "############ 5. ПОЛНЫЙ ОТВЕТ В ПРИВАТНЫЙ ФАЙЛ ############"
sudo install -d -m 0700 -o helm -g helm "$OUT"
F="$OUT/acceptance-$STAMP.json"
sudo install -m 0600 -o helm -g helm "$RESP" "$F"
shred -u "$RESP" 2>/dev/null || rm -f "$RESP"
echo "файл: $F"
sudo stat -c 'права | владелец | размер: %a | %U:%G | %s' "$F"

echo "############ ИТОГ ############"
if [ "$GATES" -eq 0 ]; then
  echo "ПРИЁМКА ПРОЙДЕНА"
else
  echo "ПРИЁМКА ПРОВАЛЕНА"
fi
exit "$GATES"
