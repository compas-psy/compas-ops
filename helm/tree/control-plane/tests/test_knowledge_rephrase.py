"""helm_core/knowledge/rephrase.py — клиент Ollama Z2-рефраза (§14.12).

Реальная Ollama недоступна в песочнице разработки (та же причина, что
у embeddings.py/embed_service.py) — `urllib.request.urlopen` мокается,
контракт (payload/fail-open) тестируется без сети.
"""

import io
import json
import urllib.error

import pytest

from helm_core.knowledge import rephrase as rephrase_module
from helm_core.knowledge.rephrase import RephraseUnavailable, rephrase, rephrase_or_none
from helm_core.knowledge.tenancy import bind_knowledge_user

from conftest import SYSTEM_OWNER_ID


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_rephrase_sends_model_prompt_and_keep_alive(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["body"] = json.loads(req.data.decode())
        return _FakeResponse({"response": "Готовый пересказ."})

    monkeypatch.setattr(rephrase_module.urllib.request, "urlopen", fake_urlopen)

    result = rephrase("вопрос", "факт", system_prompt=None)

    assert result == "Готовый пересказ."
    assert captured["body"]["model"] == rephrase_module.MODEL_NAME
    assert captured["body"]["keep_alive"] == "0"
    assert captured["body"]["stream"] is False
    assert "факт" in captured["body"]["prompt"]
    assert "system" not in captured["body"]


def test_rephrase_includes_system_prompt_when_given(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["body"] = json.loads(req.data.decode())
        return _FakeResponse({"response": "Готовый пересказ."})

    monkeypatch.setattr(rephrase_module.urllib.request, "urlopen", fake_urlopen)

    rephrase("вопрос", "факт", system_prompt="Пиши разговорно.")

    assert captured["body"]["system"] == "Пиши разговорно."


def test_rephrase_raises_on_network_error(monkeypatch):
    def fake_urlopen(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(rephrase_module.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RephraseUnavailable):
        rephrase("вопрос", "факт", system_prompt=None)


def test_rephrase_raises_on_empty_response(monkeypatch):
    def fake_urlopen(req, timeout):
        return _FakeResponse({"response": "   "})

    monkeypatch.setattr(rephrase_module.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RephraseUnavailable):
        rephrase("вопрос", "факт", system_prompt=None)


def test_rephrase_or_none_is_fail_open_on_network_error(session, monkeypatch):
    bind_knowledge_user(session, SYSTEM_OWNER_ID)

    def fake_urlopen(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(rephrase_module.urllib.request, "urlopen", fake_urlopen)

    result = rephrase_or_none(session, question="q", evidence_text="e",
                              knowledge_user_id=SYSTEM_OWNER_ID)

    assert result is None


def test_rephrase_or_none_returns_text_on_success(session, monkeypatch):
    bind_knowledge_user(session, SYSTEM_OWNER_ID)

    def fake_urlopen(req, timeout):
        return _FakeResponse({"response": "Пересказ."})

    monkeypatch.setattr(rephrase_module.urllib.request, "urlopen", fake_urlopen)

    result = rephrase_or_none(session, question="q", evidence_text="e",
                              knowledge_user_id=SYSTEM_OWNER_ID)

    assert result == "Пересказ."


def test_rephrase_or_none_passes_owner_style_prompt(session, monkeypatch):
    """SYSTEM_OWNER несёт style_profile_version=2 после миграции
    e4a7c9f2b6d1 — rephrase_or_none обязан передать OWNER_STYLE_PROMPT
    как system, не звать модель без стиля."""
    bind_knowledge_user(session, SYSTEM_OWNER_ID)
    captured = {}

    def fake_urlopen(req, timeout):
        captured["body"] = json.loads(req.data.decode())
        return _FakeResponse({"response": "Пересказ."})

    monkeypatch.setattr(rephrase_module.urllib.request, "urlopen", fake_urlopen)

    rephrase_or_none(session, question="q", evidence_text="e",
                     knowledge_user_id=SYSTEM_OWNER_ID)

    assert "system" in captured["body"]
    assert captured["body"]["system"] == rephrase_module.style_prompt_for_version(2)
