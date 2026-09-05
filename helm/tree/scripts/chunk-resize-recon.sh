#!/bin/bash
# HELM · чем станет поисковый слой, если резать его structurally.
#
# action=recon: ничего не пишет. Модель извлечения не вызывается вовсе;
# embed-сервис опрашивается только на предмет своего собственного
# предела длины — генерации нет.
#
# Три вопроса, все три нужны ДО перечанковки:
#
#   1. какой предел у embedding-модели на самом деле. Чанк длиннее
#      предела не падает — он МОЛЧА обрезается, и хвост в векторный
#      индекс не попадает. Выбирать размер чанка, не зная этого числа,
#      значит выбирать наугад;
#   2. что за чанки лежат сейчас — распределение длин, а не среднее;
#   3. что дал бы structural splitter на тех же источниках. Считается
#      в памяти, в базу не пишется ни строки.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ 1. ПРЕДЕЛ EMBEDDING-МОДЕЛИ ############"
# ОТДЕЛЬНЫЙ контейнер, не `exec` в работающий: там модель уже в памяти
# (854МБ при лимите 1200м, ADR-025), и вторая копия его уронила бы.
# `run --rm --no-deps` — тот же приём, что описан в compose для
# embed_benchmark.
sudo docker compose run --rm --no-deps -T --entrypoint python3 helm-embed - <<'PYEOF'
"""Сколько токенов модель реально принимает, прежде чем обрезать."""
from sentence_transformers import SentenceTransformer

from helm_core.knowledge.embed_service import MODEL_NAME

model = SentenceTransformer(MODEL_NAME, device="cpu")
print(f"модель:            {MODEL_NAME}")
print(f"max_seq_length:    {model.max_seq_length} токенов")
tokenizer = model.tokenizer
for length in (200, 400, 600, 900, 1500):
    sample = "Осмотр от 26.08.2026, назначен приём препарата. " * (length // 47 + 1)
    sample = sample[:length]
    count = len(tokenizer.encode(sample))
    print(f"  {length:>5} символов русского текста → {count:>4} токенов"
          f"{'  (ОБРЕЗАЕТСЯ)' if count > model.max_seq_length else ''}")
PYEOF

echo
sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Что лежит сейчас и что дал бы structural splitter."""
import statistics

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured, health_session
from helm_core.knowledge.semantic_pilot import source_text
from helm_core.knowledge.semantic_windows import split_text
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models.health_tables import HealthKnowledgeChunk
from helm_core.models.tables import KnowledgeSource

#: Кандидаты размера. Настоящий выбирается по числу из блока 1, а не
#: отсюда: это лишь то, что показывается рядом для сравнения.
CANDIDATES = (400, 700, 1000)


def summary(lengths: list[int]) -> str:
    if not lengths:
        return "пусто"
    lengths = sorted(lengths)
    return (f"n={len(lengths)} медиана={statistics.median(lengths):.0f} "
            f"среднее={statistics.mean(lengths):.0f} "
            f"p10={lengths[len(lengths) // 10]} "
            f"p90={lengths[len(lengths) * 9 // 10]} "
            f"макс={lengths[-1]}")


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
with sessionmaker(engine, expire_on_commit=False)() as session:
    tenant = bind_knowledge_user(session, None)

    print("############ 2. ЧАНКИ, КОТОРЫЕ ЛЕЖАТ СЕЙЧАС ############")
    if health_schema_configured():
        with health_session(tenant) as graph:
            now = [len(row.text) for row in
                   graph.execute(select(HealthKnowledgeChunk)).scalars().all()]
        print(f"  health: {summary(now)}")
        short = sum(1 for length in now if length < 20)
        print(f"  короче 20 символов (MIN_LEXICAL_CHUNK_CHARS): {short}")
    else:
        print("  health-схема не настроена")

    print()
    print("############ 3. ЧТО ДАЛ БЫ STRUCTURAL SPLITTER ############")
    sources = session.execute(
        select(KnowledgeSource).where(KnowledgeSource.knowledge_user_id == tenant)
    ).scalars().all()
    texts = [text for text in (source_text(source) for source in sources) if text]
    print(f"  источников с текстом: {len(texts)}")
    for limit in CANDIDATES:
        lengths = [len(piece) for text in texts for piece in split_text(text, limit=limit)]
        print(f"  limit={limit:>5}: {summary(lengths)}")
    session.rollback()
PYEOF

echo "############ ГОТОВО ############"
