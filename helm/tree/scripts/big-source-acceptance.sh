#!/usr/bin/env bash
# HELM · приёмка порционной обработки: книга разбирается, бот отвечает.
#
# ЧТО ПРОВЕРЯЕТСЯ (распоряжение владельца 07.09.2026, п.1):
#   крупный источник ставится на разбор и ПРОДВИГАЕТСЯ порциями;
#   во время разбора владелец задаёт вопросы и кладёт заметку — и то и
#   другое обслуживается, а не ждёт конца книги;
#   прогресс книги сохраняется между порциями;
#   платных вызовов для памяти нет.
#
# ЧЕРЕЗ КАКОЙ ВХОД. `/hooks/max` — настоящий пользовательский вход в ту
# же память, с тем же probe, тем же режимом оплаты и тем же тенантом
# (SYSTEM_OWNER). Это НЕ путь Telegram→Hermes→плагин: чтобы сообщение
# прошло по нему, его должен доставить сам Telegram, а у агента нет
# аккаунта Telegram. Разница названа прямо и не выдаётся за пройденную.
#
# Секрет вебхука читается на сервере и НЕ печатается (CLAUDE.md §5.4).
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

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
req = urllib.request.Request("http://127.0.0.1:8000/hooks/max",
                             data=payload.encode(), method="POST",
                             headers={"Content-Type": "application/json",
                                      "X-Max-Bot-Api-Secret": secret})
try:
    with urllib.request.urlopen(req, timeout=90) as resp:
        print(f"    HTTP {resp.status}: {resp.read().decode()[:200]}")
except Exception as exc:  # noqa: BLE001 — диагностика приёмки
    print(f"    ОШИБКА: {type(exc).__name__}: {exc}")
PYEOF
}

echo
echo "############ 1. ЧТО СЕЙЧАС В ОЧЕРЕДИ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.acceptance_probe state

echo
echo "############ 2. ПОСТАВИТЬ КНИГУ НА РАЗБОР ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.acceptance_probe rederive

echo
echo "############ 3. ПРОГРЕСС ЧЕРЕЗ МИНУТУ ############"
sleep 60
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.acceptance_probe state

echo
echo "############ 4. ВОПРОСЫ ВЛАДЕЛЬЦА ВО ВРЕМЯ РАЗБОРА ############"
i=0
for q in "какой у меня был холестерин в последний раз?" \
         "каких врачей я посещал?" \
         "по книге Линде что такое ЭОТ?"; do
  i=$((i + 1))
  echo "— вопрос $i: $q"
  start=$(date +%s)
  post_max "$q" "acc.q$i.$(date +%s)"
  echo "    заняло: $(( $(date +%s) - start )) с"
done

echo
echo "############ 5. КОРОТКАЯ ЗАМЕТКА ВО ВРЕМЯ РАЗБОРА ############"
post_max "Запомни: код от велозамка 4719, приёмочная заметка $(date +%H%M)" "acc.note.$(date +%s)"
sleep 3
echo "— и сразу вопрос по ней:"
post_max "какой код от велозамка?" "acc.recall.$(date +%s)"

echo
echo "############ 6. ПРОГРЕСС КНИГИ ПОСЛЕ ВОПРОСОВ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.acceptance_probe state

echo
echo "############ 7. ПЛАТНЫЕ ВЫЗОВЫ ЗА ЭТОТ ПРОГОН ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.acceptance_probe paid
