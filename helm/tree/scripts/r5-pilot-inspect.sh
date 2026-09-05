#!/bin/bash
# HELM v4.0 RESCUE · R5 — пересчёт уже опубликованного пилота, без записи.
#
# Зачем отдельно от `r5-pilot.sh`: первый прогон пилота (04.09.2026)
# отчитался `mentions_total = 0` — считал публичную таблицу, а health-
# источник пишет в health-зеркало. Числа были неверны, граф — нет.
# Повторять пилот ради одних только чисел значило бы завести восемь
# лишних ревизий и снова потратить час модели, поэтому здесь
# `--inspect-only`: тот же отбор источников, но читаются ТЕКУЩИЕ
# ревизии, а не создаются новые.
#
# Ничего не публикует и не коммитит: Ollama не вызывается, транзакция
# закрывается откатом.
#
# В stdout — только сводка: идентификаторы, домены и числа. Ни текста
# источников, ни подписей узлов, ни цитат (§5.2 CLAUDE.md).
set -uo pipefail
cd /opt/helm/compose || exit 1

LIMIT="${R5_PILOT_LIMIT:-8}"
DOMAINS="${R5_PILOT_DOMAINS:-}"

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
echo "источников (потолок): $LIMIT"
echo "домены: ${DOMAINS:-все}"

echo "############ ПЕРЕСЧЁТ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.semantic_pilot \
    --inspect-only --limit "$LIMIT" --domains "$DOMAINS"
RC=$?

echo "############ ГОТОВО (rc=$RC) ############"
exit "$RC"
