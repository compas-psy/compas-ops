#!/bin/bash
# Живая проверка: находит ли РЕАЛЬНЫЙ probe() HELM (тот же путь, что
# видит Telegram/MAX) содержимое уже загруженных health-документов.
# Транзакция откатывается — ничего не меняет.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail
cd /opt/helm/compose || exit 1

sudo docker compose exec -T helm-core python3 <<'PY'
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.probe import probe

engine = create_engine(get_settings().database_url)
with Session(engine) as s:
    for q in ["холестерин", "каких врачей я посещал", "какие анализы я сдавал"]:
        result = probe(s, query=q, domain="health")
        print(f"--- query={q!r} ---")
        print("outcome:", result.outcome, "| mode:", result.mode)
        for e in result.evidence[:3]:
            print("  source:", e.original_filename, "| rank:", e.rank)
            print("  text:", e.chunk_text[:250].replace("\n", " "))
    s.rollback()
PY
