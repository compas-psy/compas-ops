#!/usr/bin/env bash
# HELM · включить службу заботы: уборка §25.6 по расписанию.
#
# ЗАЧЕМ. ТЗ §25.6 требует автоматической уборки кэша сборки с целевым
# объёмом 3–5 ГБ. Прогон 452 замерил, почему её не было:
#
#   1. таймера уборки на сервере нет вовсе. Из юнитов HELM подняты
#      только helm-guardian.timer (проверки, каждые 5 мин) и
#      helm-backup.timer (бэкап, 03:30). `helm-cleanup.timer could not
#      be found`;
#   2. `/opt/helm/guardian/cleanup.sh` лежит с 29.08 и НИ РАЗУ не
#      запускался: ноль упоминаний в журнале за 30 дней;
#   3. даже если бы запускался — упал бы. На сервере docker 29.7.2, где
#      флага `--keep-storage` больше нет (есть `--max-used-space`), а
#      при `set -euo pipefail` первая же ошибка обрывала весь скрипт на
#      втором шаге из пяти.
#
# Итог: 41.29 ГБ кэша и диск на 84%. Этот скрипт ставит и включает
# юниты; исправления самого cleanup.sh приезжают обычным выкатом.
#
# ЧТО ДЕЛАЕТСЯ. Пишутся два systemd-юнита, таймер включается, уборка
# запускается один раз прямо сейчас — чтобы увидеть её работу, а не
# поверить в неё. Ничего, кроме юнитов, не создаётся и не удаляется:
# что именно удаляет уборка, решает cleanup.sh и закрытый список §25.6.
set -uo pipefail

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo
echo "############ 0. ПРОВЕРКА: ПРИЕХАЛ ЛИ ИСПРАВЛЕННЫЙ cleanup.sh ############"
if ! sudo test -x /opt/helm/guardian/cleanup.sh; then
  echo "НЕТ /opt/helm/guardian/cleanup.sh — сначала обычный выкат (action=deploy)."
  exit 1
fi
if ! sudo grep -q -- "--max-used-space" /opt/helm/guardian/cleanup.sh; then
  echo "cleanup.sh на сервере старый (в нём ещё --keep-storage, которого нет в"
  echo "docker 29.x). Включать таймер на заведомо падающий скрипт нельзя —"
  echo "сначала обычный выкат (action=deploy), он доставит исправленный."
  exit 1
fi
echo "cleanup.sh на месте и знает --max-used-space."

echo
echo "############ 1. ЮНИТЫ ############"
sudo tee /etc/systemd/system/helm-cleanup.service >/dev/null <<'UNIT_SERVICE'
[Unit]
Description=HELM · служба заботы: автоочистка по ТЗ §25.6
Documentation=file:///opt/helm/docs/OPERATIONS.md
# Уборке нужен живой docker — в отличие от Guardian, который намеренно
# работает и тогда, когда docker мёртв (§30.10). Это одна из причин,
# почему уборка — отдельный юнит, а не ещё одна проверка внутри
# guardian.py: у Guardian ProtectSystem=strict, и сокет docker ему
# недоступен на запись; Guardian ходит раз в пять минут, а уборке этого
# не нужно; и watchdog, который сам меняет состояние, перестаёт быть
# наблюдателем.
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/opt/helm/guardian/cleanup.sh --apply
User=root
Group=root
TimeoutStartSec=900

StandardOutput=journal
StandardError=journal
UNIT_SERVICE
sudo tee /etc/systemd/system/helm-cleanup.timer >/dev/null <<'UNIT_TIMER'
[Unit]
Description=Ежечасный запуск уборки §25.6

[Timer]
# ПОЧЕМУ КАЖДЫЙ ЧАС, А НЕ РАЗ В СУТКИ. Замер 07.09.2026: за один день
# кэш сборки вырос до 41.29 ГБ примерно за полсотни выкатов — около
# 0.8 ГБ на выкат. Суточная уборка означала бы ровно то, что и
# случилось: сутки роста и диск на 84%. При ежечасной пик держится в
# районе 4 ГБ цели плюс выкаты этого часа.
OnCalendar=hourly
RandomizedDelaySec=5min
Persistent=true

[Install]
WantedBy=timers.target
UNIT_TIMER
sudo systemctl daemon-reload
sudo systemctl enable --now helm-cleanup.timer
echo "helm-cleanup.timer:"
sudo systemctl is-enabled helm-cleanup.timer
sudo systemctl is-active helm-cleanup.timer

echo
echo "############ 2. ДИСК ДО ############"
df -h / | tail -1
sudo docker system df | sed -n '1p;5p'

echo
echo "############ 3. ОДИН ЗАПУСК ПРЯМО СЕЙЧАС ############"
# Проверяем работу, а не намерение: таймер, который никто не видел
# работающим, ничем не лучше отсутствующего.
sudo systemctl start helm-cleanup.service
sudo systemctl show helm-cleanup.service -p Result -p ExecMainStatus
echo "— журнал этого запуска:"
sudo journalctl -u helm-cleanup.service --since "5 min ago" --no-pager 2>&1 | tail -20

echo
echo "############ 4. ДИСК ПОСЛЕ ############"
df -h / | tail -1
sudo docker system df | sed -n '1p;5p'

echo
echo "############ 5. РАСПИСАНИЕ ############"
sudo systemctl list-timers helm-cleanup.timer --no-pager 2>&1 | head -3
