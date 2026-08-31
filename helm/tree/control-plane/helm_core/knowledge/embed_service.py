"""HELM Knowledge embedding service (ADR-025).

Отдельный процесс/образ от Control Plane (лимит 768MB, без torch вовсе)
и от воркера (Docling — другой набор моделей): резидентная модель
эмбеддингов (854MB RSS, замер 31.08.2026, `embed_benchmark.py`) не
помещается ни в один из них. Стейтлес HTTP: POST /embed
{"texts": [...]} -> {"vectors": [[...]], "dim": N}. Модель грузится один
раз при старте процесса и держится в памяти — §4.3.1 "persistent only if
resource benchmark proves safe": решение, что это безопасно, принято по
факту замера (ADR-025), не заранее.

Модель зашита константой, не настраивается переменной окружения:
конфигурируемая модель означала бы, что pgvector-колонка
(`KnowledgeChunk.embedding`, размерность фиксирована при создании типа
столбца) может молча разойтись с тем, что реально отдаёт сервис — смена
модели обязана идти вместе с новой миграцией колонки, не одной
переменной окружения в обход неё.

Никаких секретов не получает и никуда не стучится, кроме своего порта —
доступа ни к LiteLLM/OpenRouter credentials, ни к базе.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .embeddings import MAX_BATCH_SIZE

#: ADR-025 — единственный источник истины про модель этого сервиса.
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
#: Совпадает с models/tables.py::KNOWLEDGE_EMBED_DIM.
EMBED_DIM = 384


class EmbedIn(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=MAX_BATCH_SIZE)


def create_app() -> FastAPI:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_NAME, device="cpu")

    app = FastAPI()

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "model": MODEL_NAME, "dim": EMBED_DIM}

    @app.post("/embed")
    def embed(body: EmbedIn) -> dict:
        vectors = model.encode(body.texts, normalize_embeddings=True).tolist()
        return {"vectors": vectors, "dim": EMBED_DIM}

    return app
