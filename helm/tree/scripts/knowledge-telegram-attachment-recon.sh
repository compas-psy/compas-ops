#!/bin/bash
# Разведка (read-only, ничего не меняет и не деплоит): может ли
# helm-control (hermes/plugins/helm-control/__init__.py) увидеть/скачать
# вложение Telegram-сообщения — P8.5.7, спека §14.5.1 явно допускает, что
# ответ может быть "нет" (ADR-018: "if current stable Hermes plugin hook
# cannot expose/download attachment safely, implementation-agent must add
# the smallest transport adapter... Record exact solution in ADR-018").
#
# Сегодня `_on_pre_gateway_dispatch(event, gateway)` в helm-control читает
# только event.text/event.user_id/event.message_id/event.source — с
# вложением этого недостаточно. Этот скрипт ищет ТРИ вещи по реальному
# исходнику Hermes, а не по документации/памяти:
#   1. класс event — есть ли там что-то про attachment/document/photo/file,
#      или "сырой" объект апдейта Telegram, из которого можно достать file_id;
#   2. TelegramAdapter — есть ли метод скачивания файла (get_file/download),
#      и доступен ли токен бота изнутри (нужен "smallest transport adapter",
#      если штатного пути нет — тот же bot token, без второго consumer'а
#      апдейтов);
#   3. как заполняется gateway.adapters (уже частично известно из
#      hermes-recon-telegram-send.sh, F-260829-27) — на случай, если
#      понадобится дёрнуть тот же адаптер, а не создавать новый.
set -uo pipefail

HERMES_PY=/home/helm/.hermes/hermes-agent/venv/bin/python3
if [ ! -x "$HERMES_PY" ]; then
  HERMES_PY=$(command -v python3)
fi
echo "HERMES_PY=$HERMES_PY"

HERMES_SRC=$("$HERMES_PY" -c "import hermes_cli, os; print(os.path.dirname(os.path.dirname(hermes_cli.__file__)))" 2>/dev/null)
echo "HERMES_SRC=${HERMES_SRC:-НЕ НАЙДЕНО}"
if [ -z "$HERMES_SRC" ]; then
  echo "не нашли исходники Hermes автоматически — venv по другому пути?"
  echo "проверка вручную: find / -maxdepth 8 -iname 'hermes_cli' -type d 2>/dev/null"
  exit 0
fi

echo
echo "===== 1. Класс event — где определён, какие поля ====="
# pre_gateway_dispatch зовёт callback(event=..., gateway=...) — ищем, где
# gateway ЕГО создаёт/собирает (не только объявление класса, само
# заполнение полей важнее — оттуда видно, откуда взялся бы file_id).
grep -rln "class.*Event\b" "$HERMES_SRC"/gateway/*.py 2>/dev/null
EVENT_FILE=$(grep -rl "class.*Event\b" "$HERMES_SRC"/gateway/*.py 2>/dev/null | head -1)
if [ -n "$EVENT_FILE" ]; then
  echo "EVENT_FILE=$EVENT_FILE"
  echo "-- определение класса (первые 80 строк от первого совпадения) --"
  awk '/class.*Event\b/{f=1} f' "$EVENT_FILE" | head -80
fi
echo
echo "-- любые поля/атрибуты со словами attachment/document/photo/file/media/raw в gateway/*.py --"
grep -rniE "attachment|\bdocument\b|\bphoto\b|\bfile_id\b|\bmedia\b|raw_update|\.update\b" "$HERMES_SRC"/gateway/*.py 2>/dev/null | grep -v __pycache__ | head -40

echo
echo "===== 2. TelegramAdapter — методы, доступ к токену ====="
TG_FILE=$(find "$HERMES_SRC" -path "*platforms*" -iname "*telegram*" 2>/dev/null | grep -v __pycache__ | head -1)
echo "TG_FILE=${TG_FILE:-НЕ НАЙДЕНО}"
if [ -n "$TG_FILE" ]; then
  echo "-- все def в файле --"
  grep -n "    def \|class " "$TG_FILE"
  echo
  echo "-- поиск get_file/download/bot_token/self.token/self.bot --"
  grep -n "get_file\|download\|bot_token\|self\.token\|self\.bot\b\|Bot(" "$TG_FILE"
  echo
  echo "-- как адаптер создаётся (конструктор, откуда токен) --"
  awk '/def __init__\(/,/^    def [a-zA-Z_]+\(/' "$TG_FILE" | head -40
fi

echo
echo "===== 3. python-telegram-bot / aiogram — какая библиотека и версия ====="
"$HERMES_PY" -c "
import importlib
for name in ('telegram', 'aiogram'):
    try:
        m = importlib.import_module(name)
        print(name, getattr(m, '__version__', '?'))
    except ImportError:
        print(name, 'не установлен')
"

echo "DONE"
