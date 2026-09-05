#!/bin/bash
# HELM v4.0 RESCUE · R6 — разрешение сущностей на живых данных.
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
# В stdout — только числа и виды совпадений. Ни подписей сущностей, ни
# цитат: §5.2 CLAUDE.md.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ УЗЛОВ ДО ############"
sudo docker compose exec -T helm-postgres psql -U helm -d helm -tAc \
  "select count(*) from knowledge_nodes"

echo "############ СУХОЙ ПРОГОН ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution --dry-run
DRY=$?

echo "############ ЗАПИСЬ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution
RC=$?

echo "############ УЗЛОВ ПОСЛЕ ############"
# Число обязано совпасть с «до»: проход не создаёт и не удаляет узлов.
sudo docker compose exec -T helm-postgres psql -U helm -d helm -tAc \
  "select count(*) from knowledge_nodes"

echo "############ ГОТОВО (dry=$DRY rc=$RC) ############"
exit "$RC"
