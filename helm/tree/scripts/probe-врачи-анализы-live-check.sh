#!/bin/bash
# Владелец показал реальные плохие ответы в чате:
#  - "каких врачей я посещал" -> 5 совпадений "Врач КДЛ:" (это подпись
#    лаборанта на бланке анализа, не визит к врачу) вместо реальных
#    "ОСМОТР ГАСТРОЭНТЕРОЛОГА"/"Врач уролог: Кириченко..." — те самые
#    чанки, что раньше дали cosine 0.67-0.71 в отдельной проверке.
#  - "какие анализы я сдавал" -> вообще ничего не найдено, хотя 59
#    файлов анализов точно есть.
# Вызываем реальный probe() (тот же код, что в проде) в откатываемой
# транзакции с ТОЧНЫМИ формулировками владельца, печатаем ПОЛНЫЙ список
# evidence с рангами — без этого гадать про лексику/вектор бессмысленно.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

cd /opt/helm/compose
sudo docker compose exec -T helm-core python3 <<'PY'
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.probe import probe

engine = create_engine(get_settings().database_url)

for query in ["каких врачей я посещал за последний год", "какие анализы я сдавал"]:
    print(f"\n=== запрос: {query!r} ===")
    with Session(engine) as s:
        result = probe(s, query=query, domain=None)
        print(f"outcome={result.outcome} mode={result.mode}")
        if result.evidence:
            for e in result.evidence:
                preview = e.chunk_text.replace("\n", " ")[:100]
                print(f"  rank={e.rank:.4f}  файл={e.original_filename!r}  текст={preview!r}")
        else:
            print("  evidence пуст")
        s.rollback()
PY
