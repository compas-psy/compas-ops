#!/bin/bash
# HELM v4.0 RESCUE · R0, второй заход.
#
# Первый заход дал отпечаток выкаченного helm_core, который НЕ совпал ни
# с одним из последних коммитов ветки, хотя число файлов совпадает (59) и
# atomizer.py на сервере побайтово равен HEAD. Значит, расходится
# какой-то другой файл — «выкачено то же, что в HEAD» неверно, и надо
# знать, что именно расходится, а не гадать.
#
# Печатает построчный список sha256 всех .py выкаченного helm_core —
# сравнение с git делает агент у себя.
#
# Заодно: фазовый чекпоинт R0 (§31.0) и состояние точек возврата.
set -uo pipefail

echo "############ ПОФАЙЛОВЫЕ ХЭШИ ВЫКАЧЕННОГО helm_core ############"
cd /opt/helm/control-plane || exit 1
sudo find helm_core -name '*.py' -type f -print0 | LC_ALL=C sort -z | xargs -0 sudo sha256sum

echo
echo "############ ЧЕКПОИНТ R0 ############"
ls -l /opt/helm/scripts/ 2>/dev/null
if [ -x /opt/helm/scripts/checkpoint.sh ]; then
  sudo /opt/helm/scripts/checkpoint.sh create R0 "v4.0 RESCUE: заморозка перед разбором semantic-v1"
  echo "--- список чекпоинтов ---"
  sudo /opt/helm/scripts/checkpoint.sh list
else
  echo "checkpoint.sh на сервере НЕТ — deploy.yml его не доставляет (доставляются только backup.sh/restore_test.sh)"
fi

echo
echo "############ ТОЧКИ ВОЗВРАТА restic ############"
sudo ls -l /etc/helm/backup 2>/dev/null | sed 's/^/  /' || echo "  /etc/helm/backup нет"
if [ -r /etc/helm/backup/env ]; then
  # shellcheck disable=SC1091
  sudo bash -c 'set -a; . /etc/helm/backup/env; set +a; restic snapshots --latest 3 2>&1 | tail -12'
else
  echo "  конфигурации restic не видно из-под этой роли"
fi

echo
echo "############ ГОТОВО ############"
