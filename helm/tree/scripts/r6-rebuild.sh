#!/bin/bash
# HELM v4.0 RESCUE · R6 — пересборка производных таблиц личности.
#
# ЗАПУСКАТЬ ТОЛЬКО через action=maintenance: скрипт пишет, и точка
# возврата снимается тем же гейтом, что у выката. Через `recon` —
# нельзя: recon это диагностика.
#
# Владелец, 05.09.2026 разрешил пересобрать РОВНО три производные
# таблицы для тенанта владельца: identities, identity_members,
# resolution_candidates. Они пересчитываются из узлов, поэтому удаление
# ничего не теряет безвозвратно. Исходные nodes/mentions/provenance/
# semantic runs/sources не трогаются — это проверяет инвариант внутри
# прохода, а не обещание здесь.
#
# Нужно потому, что правила тождества изменились (однословная подпись
# PERSON больше не доказательство, алиас больше не сливает), а проход
# идемпотентен и уже отнесённый узел не пересматривает: без пересборки
# старый состав остался бы старым.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
echo "############ ПРОВЕРКА ДО ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution --probe

echo "############ ПЕРЕСБОРКА ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution --rebuild
RC=$?

# Машинная проверка идемпотентности, а не «повтор и посмотрим глазами».
# Прошлая редакция запускала обычный второй проход и возвращала код
# ПЕРВОГО — то есть несоблюдение осталось бы в логе и не остановило бы
# цепочку. Теперь проверка живёт в Python (`--verify-idempotent`): сухой
# проход, ненулевой код возврата, если он создал бы хоть одну строку.
echo "############ ИДЕМПОТЕНТНОСТЬ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution \
    --verify-idempotent
IDEM=$?

# Второй гейт: остаток однословных личностей-людей с составом. Без него
# возможно идеально идемпотентное, но неправильное состояние — проход
# ничего не создаёт, а legacy-строки, которых он сегодня не создал бы,
# в таблицах остались. `--probe` для этого не годится: он печатает и
# возвращает ноль, оставаясь диагностикой.
echo "############ ОСТАТОК ОДНОСЛОВНЫХ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution \
    --verify-no-weak-person-members
RESIDUE=$?

echo "############ ГОТОВО (rc=$RC idem=$IDEM residue=$RESIDUE) ############"
# Провал любой из трёх половин валит шаг. Возвращать код одной только
# пересборки значило бы объявлять успехом прогон с недоказанной
# идемпотентностью или с оставшимся мусором.
if [ "$RC" -ne 0 ]; then exit "$RC"; fi
if [ "$IDEM" -ne 0 ]; then exit "$IDEM"; fi
exit "$RESIDUE"
