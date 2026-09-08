#!/usr/bin/env bash
# HELM · пользовательская приёмка: фактический запрос → фактический ответ.
#
# ЗАЧЕМ ЭТОТ СКРИПТ ОТДЕЛЬНО ОТ ПРЕЖНИХ. Распоряжение владельца
# 07.09.2026, п.7: «Показывай фактический запрос, ответ, источник,
# развёрнутый SHA и оставшееся ограничение». Прежняя приёмка печатала
# код HTTP и исход (`local_answer`) — по ним не видно ни ответа, ни
# источников, ни оговорок. Здесь после каждого вопроса читается ОЧЕРЕДЬ
# ИСХОДЯЩИХ: там лежит ровно тот текст, который владелец увидит в боте.
#
# ЧЕРЕЗ КАКОЙ ВХОД. `/hooks/max` — настоящий пользовательский вход в ту
# же память, тот же probe, тот же режим оплаты, тот же тенант. Это НЕ
# путь Telegram→Hermes→плагин: чтобы сообщение прошло по нему, его
# должен доставить сам Telegram, а аккаунта Telegram у агента нет.
# Ограничение названо прямо и не выдаётся за пройденное.
#
# Секреты читаются на сервере и НЕ печатаются (CLAUDE.md §5.4).
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "############ ВЫКАЧЕНО ############"
echo "SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

CHAT_ID=777

post_max() {
  local text="$1" mid="$2"
  local secret payload
  secret=$(sudo cat /etc/helm/secrets/max_webhook_secret 2>/dev/null || echo "")
  payload=$(python3 -c '
import json, sys
print(json.dumps({"update_type": "message_created", "message": {
    "sender": {"user_id": int(sys.argv[3])},
    "recipient": {"chat_id": int(sys.argv[4]), "chat_type": "dialog"},
    "body": {"mid": sys.argv[2], "seq": 1, "text": sys.argv[1]}}}))
' "$text" "$mid" "$(sudo cat /etc/helm/secrets/max_owner_id 2>/dev/null || echo 0)" "$CHAT_ID")
  sudo docker compose exec -T helm-core python3 - "$payload" "$secret" <<'PYEOF'
import json, sys, urllib.request
payload, secret = sys.argv[1], sys.argv[2]
req = urllib.request.Request("http://127.0.0.1:8080/hooks/max",
                             data=payload.encode(), method="POST",
                             headers={"Content-Type": "application/json",
                                      "X-Max-Bot-Api-Secret": secret})
try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        print(f"    исход: {resp.read().decode()[:120]}")
except Exception as exc:  # noqa: BLE001 — диагностика приёмки
    print(f"    ОШИБКА: {type(exc).__name__}: {exc}")
PYEOF
}

ask() {
  local text="$1"
  echo
  echo "── ЗАПРОС: $text"
  local start; start=$(date +%s)
  post_max "$text" "ua.$(date +%s%N)"
  echo "    заняло: $(( $(date +%s) - start )) с"
  echo "    ОТВЕТ, который увидит владелец:"
  sudo docker compose exec -T helm-core \
    python3 -m helm_core.knowledge.acceptance_probe outbox 1
}

echo
echo "############ 1. КРУПНЫЙ ИСТОЧНИК: ЧЕМ КОНЧИЛОСЬ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.acceptance_probe state

echo
echo "############ 2. СЛУЖБА ЗАБОТЫ: ОТМЕТКИ И ТАЙМЕР ############"
sudo systemctl list-timers helm-cleanup.timer --all --no-pager 2>&1 | head -3
for marker in last-cleanup last-cleanup-failed; do
  path="/var/lib/helm-guardian/$marker"
  if sudo test -e "$path"; then
    echo "  $marker: $(sudo stat -c %y "$path")"
  else
    echo "  $marker: нет"
  fi
done
sudo docker compose exec -T helm-core python3 -c "
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=10) as r:
    print('  health:', r.read().decode()[:200])
" 2>&1 | tail -2

echo
echo "############ 3. ВОПРОСЫ ВЛАДЕЛЬЦА ЧЕРЕЗ БОТ ############"
ask "какой у меня был холестерин в последний раз?"
ask "по книге Линде что такое ЭОТ?"
ask "чем отличается страховка поездки от страховки квартиры"
ask "дай примеры работы с неуверенностью"

echo
echo "############ 4. ВЫДАЧА ОРИГИНАЛА ############"
ask "отдай оригинал заключения"

echo
echo "############ 5. НОВАЯ ЗАМЕТКА И ЕЁ ИЗВЛЕЧЕНИЕ ############"
ask "Запомни: код от велозамка 4719, приёмочная заметка $(date +%H%M)"
ask "какой код от велозамка?"

echo
echo "############ 6. ПЛАТНЫЕ ВЫЗОВЫ ЗА ЧАС ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.acceptance_probe paid
