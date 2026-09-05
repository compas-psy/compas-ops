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

# Через `docker exec` по имени контейнера, а не `docker compose exec` по
# имени сервиса: сервиса `helm-postgres` в compose нет, и первый прогон
# (270) молча напечатал «service is not running» вместо числа — то есть
# инвариант, ради которого счётчик и заведён, не проверился.
count_nodes() {
  sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc \
    "select count(*) from knowledge_nodes"
}

echo "############ УЗЛОВ ДО ############"
BEFORE=$(count_nodes)
echo "$BEFORE"

echo "############ СУХОЙ ПРОГОН ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution --dry-run
DRY=$?

echo "############ ЗАПИСЬ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution
RC=$?

echo "############ УЗЛОВ ПОСЛЕ ############"
AFTER=$(count_nodes)
echo "$AFTER"

# Не отчёт, а условие. «Исходные nodes не мутировать и не удалять» —
# распоряжение владельца; напечатать два числа и не сравнить их значило
# бы оставить проверку на внимательность читателя лога.
if [ "$BEFORE" != "$AFTER" ]; then
  echo "::error::узлов было $BEFORE, стало $AFTER — проход тронул исходные данные"
  exit 1
fi
echo "узлы не изменились: $AFTER"

echo "############ ГОТОВО (dry=$DRY rc=$RC) ############"
exit "$RC"
