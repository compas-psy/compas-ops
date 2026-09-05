#!/bin/bash
# HELM v4.0 RESCUE · R7 — первая живая приёмка: «каких врачей я посещал?».
#
# Запускается через action=recon: исполнитель только читает, транзакция
# закрывается откатом. Ни узлов, ни упоминаний, ни личностей он не
# трогает.
#
# ИМЕНА В ЛОГ НЕ ПОПАДАЮТ. В stdout уходит сводка без содержимого: числа,
# флаг доказанности специальности, число доказательств на каждый пункт
# (§5.2 CLAUDE.md). Полный ответ — с ФИО и цитатами — пишется файлом на
# сервере рядом с самим корпусом, в том же периметре, где уже лежат
# исходные документы; новой площадки для медицинских данных здесь не
# заводится.
set -uo pipefail
cd /opt/helm/compose || exit 1

QUESTION='каких врачей я посещал?'
DIR=/opt/helm/r7
OUT="$DIR/answer-$(date -u +%Y%m%dT%H%M%SZ).json"

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
sudo mkdir -p "$DIR"
sudo chmod 700 "$DIR"

echo "############ ВОПРОС ############"
echo "$QUESTION"

echo "############ СВОДКА (без имён) ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.query_router \
    --question "$QUESTION" --out /tmp/r7-answer.json
RC=$?

if [ "$RC" -eq 0 ]; then
  # Полный ответ переносится на хост и в лог не печатается ни разу.
  sudo docker compose exec -T helm-core cat /tmp/r7-answer.json \
      | sudo tee "$OUT" > /dev/null
  sudo chmod 600 "$OUT"
  sudo docker compose exec -T helm-core rm -f /tmp/r7-answer.json
  echo "############ ПОЛНЫЙ ОТВЕТ ############"
  echo "$OUT — $(sudo cat "$OUT" | wc -c) байт"
fi

echo "############ ГОТОВО (rc=$RC) ############"
exit "$RC"
