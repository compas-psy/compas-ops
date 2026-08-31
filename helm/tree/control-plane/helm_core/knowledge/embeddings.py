"""Клиент HELM Knowledge embedding-сервиса (ADR-025).

`helm-core` (Probe, query-time) и воркер (ingest, passage-time)
используют ОДИН И ТОТ ЖЕ клиент — модель и её HTTP-контракт одни на оба
процесса, расхождение в клиентском коде было бы источником незаметного
дрейфа. Ни один из двух процессов не тянет torch в свой образ:
резидентная модель (854MB RSS, замер 31.08.2026) не помещается в
helm-core (лимит 768MB), а воркер уже занят Docling — отдельный сервис
`helm-embed`, стейтлес HTTP.

Эмбеддинг — дополнение к лексическому поиску, не его замена (§14.12
"FTS + pgvector", не "pgvector вместо FTS"): недоступность сервиса не
должна ронять ни ingest, ни Probe.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

#: Имя сервиса из docker-compose.yml — Docker Compose DNS резолвит его
#: внутри общей bridge-сети, ни helm-core, ни воркер не используют
#: network_mode: host (в отличие от litellm/caddy), поэтому service-name,
#: не 127.0.0.1.
EMBED_URL = "http://helm-embed:8090/embed"
REQUEST_TIMEOUT = 30
#: Совпадает с EmbedIn.texts max_length в embed_service.py — большие
#: пакеты режутся на этой стороне, а не падают на стороне сервиса.
MAX_BATCH_SIZE = 64


class EmbedServiceUnavailable(RuntimeError):
    """Эмбеддинг-сервис недоступен или ответил ошибкой."""


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Векторы в том же порядке, что texts.

    Поднимает `EmbedServiceUnavailable` при сбое — вызывающая сторона
    решает, откатываться на чисто лексический путь (fail-open) или нет.
    """
    if not texts:
        return []
    vectors: list[list[float]] = []
    for i in range(0, len(texts), MAX_BATCH_SIZE):
        batch = texts[i:i + MAX_BATCH_SIZE]
        body = json.dumps({"texts": batch}).encode("utf-8")
        req = urllib.request.Request(
            EMBED_URL, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                result = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EmbedServiceUnavailable(str(exc)) from exc
        vectors.extend(result["vectors"])
    return vectors


def embed_texts_or_none(texts: list[str]) -> list[list[float] | None]:
    """Fail-open обёртка для ingest (worker.py/ingest.py): недоступность
    сервиса не должна останавливать индексацию целиком — чанк остаётся
    без embedding (лексический поиск по нему всё равно работает, §14.12
    "FTS + pgvector"), а не превращает job в FAILED."""
    try:
        return embed_texts(texts)
    except EmbedServiceUnavailable as exc:
        logger.warning("embed service недоступен, чанки останутся без embedding: %s", exc)
        return [None] * len(texts)
