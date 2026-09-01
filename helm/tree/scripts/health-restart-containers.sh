#!/bin/bash
# ADR-005/P12, шаг 3 из 4. Секрет health_database_url читается ОДИН РАЗ
# при старте процесса (helm_core/config.py::_resolve_file_env_vars) —
# после того как scripts/setup-health-role.sh заполнил файл реальным
# паролем, helm-core и helm-knowledge-worker обязаны быть перезапущены,
# иначе продолжат работать со старым (пустым) значением из памяти.
# Обычный restart, не force-recreate — секрет читается из уже
# смонтированного bind-mount заново, пересборка образа не нужна.
# Запускается на сервере: bash /tmp/recon.sh
set -euo pipefail
cd /opt/helm/compose

sudo docker compose restart helm-core helm-knowledge-worker
sleep 5
sudo docker compose ps helm-core helm-knowledge-worker

echo "=== health_database_url теперь виден внутри контейнера (непустой = ок) ==="
sudo docker compose exec -T helm-core sh -c \
  '[ -s /run/secrets/health_database_url ] && echo "непусто" || echo "ПУСТО — setup-health-role.sh ещё не прогнан?"'
