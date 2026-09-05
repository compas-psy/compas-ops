#!/bin/bash
# HELM v4.0 RESCUE · R6 — разрешение сущностей на живых данных.
#
# ЗАПУСКАТЬ ТОЛЬКО через action=maintenance: скрипт пишет. Через `recon`
# — нельзя, recon это диагностика (разделение заведено 05.09.2026;
# прогоны 270/271 шли ещё через recon, когда действия не было).
#
# Распоряжение владельца 05.09.2026: «R6 — identity only... Auto-resolution
# только по strong identity... Исходные nodes/mentions/provenance не
# мутировать и не удалять.»
#
# Прогон АДДИТИВЕН по построению: пишутся только строки трёх новых
# таблиц (личность, состав, кандидат). Ни один узел, ни одно упоминание
# не меняется и не удаляется — обратный ход это удаление новых строк,
# восстанавливать нечего.
#
# Сначала сухой прогон, потом запись. Разойдись их числа — это дефект
# самого прохода, и виден он будет до того, как что-то записано.
#
# Число узлов сверяет сам проход, до и после, в той же схеме и под тем же
# RLS (`resolve_in`), и падает при расхождении. Считать его отсюда, из
# psql, бессмысленно: health-узлы лежат в зеркале, и счётчик по public
# показал бы ноль, ничего не проверив — прогон 271 так и сделал.
#
# В stdout — только числа и виды совпадений. Ни подписей сущностей, ни
# цитат: §5.2 CLAUDE.md.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ СУХОЙ ПРОГОН ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution --dry-run
DRY=$?

echo "############ ЗАПИСЬ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution
RC=$?

# Повтор — не перестраховка, а проверка идемпотентности на живых данных
# (владелец, 05.09.2026: «повтор => идемпотентно»). Второй проход обязан
# показать identities_created / members_created / candidates_created = 0
# и already_resolved = числу состава: значит, строки первого прохода
# лежат в базе и прочитаны обратно, а не заведены заново.
echo "############ ПОВТОР ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution
AGAIN=$?

# Та же проверка, что шла до записи: однословных личностей-людей с
# составом больше одного узла не должно появиться.
echo "############ ПРОВЕРКА ПОСЛЕ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution --probe
PROBE=$?

echo "############ ГОТОВО (dry=$DRY rc=$RC again=$AGAIN probe=$PROBE) ############"
exit "$RC"
