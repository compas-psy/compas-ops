"""R4 (§14.18) — config-wiring модели/keep_alive извлекателя, регрессия.

До R4 модель извлекателя была вшита в сигнатуру `publish_semantic_run`
(`model: str = DEFAULT_MODEL`), а `keep_alive` — голой константой внутри
`semantic_extract.py`. Оба значения были неявным архитектурным решением,
а не выбором (замер R4 выбирает и то, и другое). Требование владельца:
«Код по-прежнему принимает model externally. Добавить regression fixture,
чтобы случайное возвращение к hardcoded model ловилось тестом».

Эти тесты ловят именно откат: если кто-то вернёт бывший хардкод вместо
чтения `get_settings()`, они покраснеют — единственная причина, по
которой оба значения здесь переопределены переменной окружения на
заведомо неправдоподобную строку, которую с хардкодом получить неоткуда.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from helm_core.config import get_settings
from helm_core.knowledge import semantic_extract
from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.semantic_extract import WindowExtraction
from helm_core.knowledge.semantic_publish import publish_semantic_run
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSemanticRun

from conftest import SYSTEM_OWNER_ID


def _no_op_extractor(window_text, *, domain, heading_path=(), model=""):
    return WindowExtraction()


def test_publish_semantic_run_reads_model_from_settings_not_a_literal(session, monkeypatch):
    monkeypatch.setenv("HELM_KNOWLEDGE_SEMANTIC_MODEL", "r4-regression-model")
    get_settings.cache_clear()
    try:
        bind_knowledge_user(session, SYSTEM_OWNER_ID)
        source = ingest_text(session, domain="personal", text="Текст без семантики.")
        session.flush()

        result = publish_semantic_run(session, source=source, text="Текст без семантики.",
                                      extract=_no_op_extractor)

        run = session.scalars(select(KnowledgeSemanticRun).where(
            KnowledgeSemanticRun.id == result.run_id)).one()
        assert run.extractor_model == "r4-regression-model"
    finally:
        get_settings.cache_clear()


def test_call_ollama_reads_keep_alive_from_settings_not_a_literal(monkeypatch):
    monkeypatch.setenv("HELM_KNOWLEDGE_SEMANTIC_KEEP_ALIVE", "r4-regression-keep-alive")
    get_settings.cache_clear()
    captured: dict = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self):
            return json.dumps({"response": "{}"}).encode()

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode())
        return _FakeResponse()

    monkeypatch.setattr(semantic_extract.urllib.request, "urlopen", fake_urlopen)
    try:
        semantic_extract._call_ollama("окно", model="gemma2:2b")
        assert captured["body"]["keep_alive"] == "r4-regression-keep-alive"
    finally:
        get_settings.cache_clear()
