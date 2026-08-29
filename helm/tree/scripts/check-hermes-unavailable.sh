#!/bin/bash
# Разовая диагностика: почему hermes_bridge.deliver() падает на живых
# сообщениях MAX. Причина падения раньше писалась ТОЛЬКО в TaskEvent
# (БД), не в docker logs (F, найдено 29.08.2026 при живом тестировании
# MAX уже после включения API Hermes) — обычный просмотр лога ничего
# не показывал. hooks.py теперь дополнительно логирует тип исключения;
# этот скрипт читает полный текст причины напрямую из БД.
#
# Запуск: sudo bash /tmp/check-hermes-unavailable.sh
set -euo pipefail
docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select timestamp, payload_redacted from task_events where event_type = 'task.hermes_unavailable' order by timestamp desc limit 5;"
