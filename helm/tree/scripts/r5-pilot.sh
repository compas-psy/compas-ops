#!/bin/bash
# HELM v4.0 RESCUE · R5 — пилотная сборка семантики на реальных источниках.
#
# Спека R5: «5–10 real sources, staging then small commit. Manual/golden
# review before full corpus.» Полный корпус — R8, и он идёт только после
# ревью результатов этого пилота владельцем.
#
# Прогон идёт тем путём, который аттестован R4: node-only извлечение
# (`extract_nodes_window`) плюс детерминированный компилятор рёбер. До R5
# продакшн звал старый edge-aware `extract_window()`, то есть выкатывал
# НЕ то, что мерили гейты; см. R5.1 в semantic_publish.py.
#
# Прогон НЕ разрушающий по построению (§14.20): каждая ревизия пишется
# отдельным `semantic run`, прежняя остаётся текущей, пока новая не
# дошла до READY, а переключение — один UPDATE указателя, обратимый.
# Точки возврата тоже на месте: выкат перед этим сделал локальный
# чекпоинт.
#
# В stdout уходит ТОЛЬКО сводка: идентификаторы, домены и числа. Ни
# текста источников, ни подписей узлов, ни цитат — §5.2 CLAUDE.md и п.7
# разбора R4 запрещают выносить содержимое личного архива в логи. Само
# содержимое остаётся на сервере, там владелец его и смотрит.
set -uo pipefail
cd /opt/helm/compose || exit 1

LIMIT="${R5_PILOT_LIMIT:-8}"
DOMAINS="${R5_PILOT_DOMAINS:-}"
OUT_DIR=/opt/helm-state/benchmarks/r5-pilot
RUN_ID="r5pilot-$(date -u +%Y%m%dT%H%M%SZ)"

sudo mkdir -p "$OUT_DIR"
sudo chown "$(id -u):$(id -g)" "$OUT_DIR"

GIT_SHA=$(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo "unknown")
echo "выкачено: $GIT_SHA"
echo "run_id: $RUN_ID"
echo "источников в пилоте (потолок): $LIMIT"
echo "домены: ${DOMAINS:-все}"

echo "############ ЗДОРОВЬЕ ДО ПИЛОТА ############"
sudo docker compose ps --format '{{.Service}} {{.Status}}' | sed 's/^/  /'

echo "############ ПИЛОТ ############"
# --out пишется ВНУТРИ контейнера: /opt/helm-state/benchmarks не
# примонтирован в helm-core (проверено при R4 p.7), поэтому отчёт
# забирается наружу отдельным `docker compose cp`.
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.semantic_pilot \
    --limit "$LIMIT" --domains "$DOMAINS" --out /tmp/r5-pilot.json
RC=$?

if [ "$RC" -eq 0 ]; then
  # Тот же способ, которым r4-final-acceptance.sh забирает свой
  # raw_diagnostics.json: по имени сервиса, а не по id контейнера —
  # проверено живыми прогонами.
  sudo docker compose cp helm-core:/tmp/r5-pilot.json "$OUT_DIR/$RUN_ID.json" \
    && echo "отчёт: $OUT_DIR/$RUN_ID.json" \
    || echo "отчёт не забран из контейнера (сам прогон при этом отработал)"
fi

echo "############ ЗДОРОВЬЕ ПОСЛЕ ПИЛОТА ############"
sudo docker compose ps --format '{{.Service}} {{.Status}}' | sed 's/^/  /'

echo "############ ГОТОВО (rc=$RC) ############"
exit "$RC"
