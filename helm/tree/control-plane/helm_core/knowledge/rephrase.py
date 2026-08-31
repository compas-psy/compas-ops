"""Клиент Ollama Z2-рефраза (§14.12, docs/KNOWLEDGE_MODELS.md).

`gemma2:2b` выбран живым замером 31.08.2026 — единственный из трёх
протестированных кандидатов без языкового глюка на стилизованном
рефразе (qwen2.5:3b/llama3.2:3b съезжали на китайский/английский
посреди русского предложения). one-shot runtime:
`OLLAMA_KEEP_ALIVE=0` в docker-compose.yml выгружает веса сразу после
ответа — вызов синхронный (Probe отвечает живому пользователю в
request path, не фоновая задача, как GigaAM), холодная латентность
(5-8с на замере) — известная цена этого архитектурного выбора,
задокументированная, не забытая.

Модель видит ТОЛЬКО evidence-текст + вопрос (+ системный промпт стиля)
— никогда не весь vault и не инструменты: узкая, управляемая роль
("перефразируй то, что уже нашли и процитировали Z0/Z1"), не свободная
генерация — то, что снижает риск галлюцинации относительно "свободного"
Z2 (см. design note в KNOWLEDGE_MODELS.md).

Fail-open (тот же паттерн, что embeddings.py::embed_texts_or_none):
недоступность Ollama не должна ронять Probe — Z0/Z1-текст уходит
владельцу как есть, корректный деградированный путь, не ошибка.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .style import style_prompt_for_version
from ..models import KnowledgeUser, KnowledgeUserRole

logger = logging.getLogger(__name__)

#: Имя сервиса из docker-compose.yml — тот же принцип, что EMBED_URL в
#: embeddings.py: Docker Compose DNS резолвит его внутри общей
#: bridge-сети.
OLLAMA_URL = "http://ollama:11434/api/generate"
#: Единственный источник истины про модель — docs/KNOWLEDGE_MODELS.md
#: фиксирует замер, который к этому привёл.
MODEL_NAME = "gemma2:2b"
REQUEST_TIMEOUT = 20
#: "0" — веса выгружаются немедленно после ответа, тот же "on-demand,
#: не резидентно" принцип, что уже применён к GigaAM (ADR-021) и явно
#: требуется спекой для Z2 (docs/KNOWLEDGE_MODELS.md).
KEEP_ALIVE = "0"


class RephraseUnavailable(RuntimeError):
    """Ollama недоступна, ответила ошибкой, или вернула пустой текст."""


def _build_prompt(question: str, evidence_text: str) -> str:
    return (
        f"Вопрос: {question}\n"
        f"Факт: {evidence_text}\n\n"
        "Перефразируй факт как прямой ответ на вопрос."
    )


def rephrase(question: str, evidence_text: str, *, system_prompt: str | None) -> str:
    """Поднимает `RephraseUnavailable` при сбое — вызывающая сторона
    решает, откатываться на Z0/Z1-текст (fail-open) или нет."""
    body: dict = {
        "model": MODEL_NAME,
        "prompt": _build_prompt(question, evidence_text),
        "stream": False,
        "keep_alive": KEEP_ALIVE,
    }
    if system_prompt:
        body["system"] = system_prompt
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            result = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RephraseUnavailable(str(exc)) from exc
    text = (result.get("response") or "").strip()
    if not text:
        raise RephraseUnavailable("ollama вернула пустой ответ")
    return text


def _owner_style_prompt_or_none(session: Session, knowledge_user_id: uuid.UUID) -> str | None:
    """`style_profile_version` сама по себе — просто число, не ключ по
    пользователю: `OWNER_STYLE_PROMPT` — голос ИМЕННО владельца, его
    нельзя подставлять кому-то ещё только потому, что в их колонке
    случайно оказалось то же число. Пока style.py несёт единственный
    профиль (владельца), стиль применяется, только если это ЕЩЁ и
    SYSTEM_OWNER — когда P8.6.6 заведёт профиль второго пользователя,
    это станет отдельным per-user lookup, не расширением этой проверки.
    """
    row = session.execute(
        select(KnowledgeUser.role, KnowledgeUser.style_profile_version)
        .where(KnowledgeUser.id == knowledge_user_id)
    ).first()
    if row is None or row.role != KnowledgeUserRole.SYSTEM_OWNER:
        return None
    return style_prompt_for_version(row.style_profile_version)


def rephrase_or_none(session: Session, *, question: str, evidence_text: str,
                     knowledge_user_id: uuid.UUID) -> str | None:
    """Fail-open обёртка для probe.py: `None` — тот же корректный
    деградированный путь, что "модель не прошла бенчмарк" в
    KNOWLEDGE_MODELS.md — Z0/Z1-текст уходит владельцу как есть, а не
    падает вся Probe."""
    system_prompt = _owner_style_prompt_or_none(session, knowledge_user_id)
    try:
        return rephrase(question, evidence_text, system_prompt=system_prompt)
    except RephraseUnavailable as exc:
        logger.warning("ollama rephrase недоступен, уходит Z0/Z1-текст как есть: %s", exc)
        return None
