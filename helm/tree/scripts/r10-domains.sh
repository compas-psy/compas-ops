#!/bin/bash
# HELM v4.0 RESCUE · R10 — приёмка §30.8.5 I на четырёх непохожих доменах.
#
# action=recon: прогон публикует четыре фикстуры продакшн-путём и
# ОТКАТЫВАЕТ всё до единой строки. После него в корпусе не появляется ни
# источника, ни узла, ни ревизии — это свойство печатается числами «до»
# и «после», а не обещается.
#
# Локальная модель при этом работает по-настоящему: проверяется ядро, а
# не заглушка.
#
# Наружу — числа и ключи фикстур. Фикстуры синтетические и никого не
# описывают, но полный отчёт всё равно кладётся файлом рядом с корпусом,
# а в лог идёт только сводка.
set -uo pipefail
cd /opt/helm/compose || exit 1

DIR=/opt/helm/r10
OUT="$DIR/domains-$(date -u +%Y%m%dT%H%M%SZ).json"

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
sudo mkdir -p "$DIR"
sudo chmod 750 "$DIR"

echo "############ ЧЕТЫРЕ ДОМЕНА ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.r10_acceptance \
    --out /tmp/r10-domains.json
RC=$?

sudo docker compose exec -T helm-core cat /tmp/r10-domains.json 2>/dev/null \
    | sudo tee "$OUT" > /dev/null
sudo chmod 640 "$OUT"
sudo docker compose exec -T helm-core rm -f /tmp/r10-domains.json
echo "полный отчёт: $OUT"

echo "############ ГОТОВО (rc=$RC) ############"
exit "$RC"
