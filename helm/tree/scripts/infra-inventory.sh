#!/usr/bin/env bash
# HELM · что на сервере есть на самом деле. Только чтение.
#
# ЗАЧЕМ. `implementation-state/ROADMAP-TO-DONE.md` говорит «419 тестов
# зелёных, ни один шаг v3.7/v3.8 на сервер не выкачен» — при том, что
# тестов 1542 и выкатов больше полусотни. Двигаться по плану, который
# расходится с реальностью, значит работать по догадке.
#
# Четыре вопроса, каждый про то, что владелец назвал вехой B:
#   1. какие службы подняты и здоровы;
#   2. n8n: есть ли в нём хоть один сценарий, или он просто поднят;
#   3. Forgejo: есть ли в нём репозитории, или он пуст;
#   4. чем занят диск и сколько его осталось.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
echo "аптайм:  $(uptime)"

echo
echo "############ 1. СЛУЖБЫ ############"
sudo docker compose ps --format 'table {{.Service}}\t{{.Status}}' 2>&1 | head -25

echo
echo "############ 2. N8N ############"
if sudo docker compose ps --services 2>/dev/null | grep -qx n8n; then
  echo "— версия и состояние:"
  sudo docker compose exec -T n8n n8n --version 2>&1 | tail -1
  echo "— сценарии в базе n8n:"
  sudo docker exec helm-postgres-1 psql -U helm -d n8n -tAc \
    "select count(*) from workflow_entity" 2>&1 | tail -1
  echo "— активные сценарии:"
  sudo docker exec helm-postgres-1 psql -U helm -d n8n -tAc \
    "select count(*) from workflow_entity where active" 2>&1 | tail -1
  echo "— выполнения за всё время:"
  sudo docker exec helm-postgres-1 psql -U helm -d n8n -tAc \
    "select count(*) from execution_entity" 2>&1 | tail -1
else
  echo "  службы n8n в compose нет"
fi

echo
echo "############ 3. FORGEJO ############"
if sudo docker compose ps --services 2>/dev/null | grep -qx forgejo; then
  echo "— репозитории:"
  sudo docker exec helm-postgres-1 psql -U helm -d forgejo -tAc \
    "select coalesce(string_agg(owner_name || '/' || name, ', '), 'ПУСТО') from repository" 2>&1 | tail -1
  echo "— зеркала (push mirror):"
  sudo docker exec helm-postgres-1 psql -U helm -d forgejo -tAc \
    "select count(*) from push_mirror" 2>&1 | tail -1
  echo "— пользователи:"
  sudo docker exec helm-postgres-1 psql -U helm -d forgejo -tAc \
    "select count(*) from \"user\"" 2>&1 | tail -1
else
  echo "  службы forgejo в compose нет"
fi

echo
echo "############ 4. ДИСК ############"
df -h / | tail -2
echo "— крупнейшее в /opt/helm:"
sudo du -sh /opt/helm/* 2>/dev/null | sort -rh | head -8
