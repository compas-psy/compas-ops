#!/bin/bash
# HELM v4.0 RESCUE · R9 — сборка производного графа знаний.
#
# ЗАПУСКАТЬ через action=maintenance: скрипт пишет файлы. В базу не
# пишет ничего — модуль только читает, транзакция закрывается откатом.
#
# Вывод производный и удаляемый: снести дерево `derived/graphify` и
# `semantic/` можно в любой момент, следующая сборка восстановит их из
# канонических таблиц. Именно поэтому это maintenance, а не destructive.
#
# Сначала сухой прогон: числа видны до того, как что-то записано.
#
# В stdout — только числа и пути. Ни подписей сущностей, ни цитат:
# health-разметка остаётся в приватном дереве (§5.2 CLAUDE.md).
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ СУХОЙ ПРОГОН ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.knowledge_graphify --dry-run
DRY=$?

echo "############ СБОРКА ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.knowledge_graphify
RC=$?

echo "############ ПОВТОР (воспроизводимость) ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.knowledge_graphify
AGAIN=$?

echo "############ ЧТО ЛЕЖИТ НА ДИСКЕ ############"
echo "файлов в общем дереве graphify:"
sudo find /opt/helm-knowledge/derived/graphify -type f 2>/dev/null | wc -l
echo "файлов в приватном дереве graphify:"
sudo find /opt/helm-knowledge-private -path '*derived/graphify*' -type f 2>/dev/null | wc -l
echo "файлов разметки в приватном дереве:"
sudo find /opt/helm-knowledge-private -path '*/semantic/*' -name '*.md' -type f 2>/dev/null | wc -l
echo "health-файлов, утёкших в ОБЩЕЕ дерево (ожидается 0):"
sudo find /opt/helm-knowledge -path '*/semantic/*' -name '*.md' -type f 2>/dev/null | wc -l

echo "############ ГОТОВО (dry=$DRY rc=$RC again=$AGAIN) ############"
exit "$RC"
