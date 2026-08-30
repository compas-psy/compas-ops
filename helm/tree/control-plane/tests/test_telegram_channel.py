"""Telegram, исходящая часть outbox (§10.3) — см. helm_core/channels/telegram.py.

Найдено живьём 30.08.2026 (первый живой тест Telegram-стороны P8.5.7):
уведомление о завершении разбора файла (`helm-knowledge-worker`, отдельный
контейнер) не доходило до владельца вовсе — `app.state.senders` знал
только про "max", доставщик молча помечал сообщение FAILED. Тесты здесь
защищают именно это: HTTP-форма запроса к Bot API и то, что `create_app()`
реально регистрирует отправитель для "telegram".
"""

import json
from unittest.mock import patch

import pytest

from helm_core.app import create_app
from helm_core.channels.telegram import TelegramSender
from helm_core.config import Settings

from conftest import DB_URL, OWNER_ID, POLICY_PATH

CHAT_ID = "123456789"


def test_telegram_sender_raises_without_token():
    with pytest.raises(RuntimeError):
        TelegramSender("")(CHAT_ID, "привет")


def test_telegram_sender_posts_chat_id_and_text_to_bot_api():
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        return FakeResponse()

    with patch("helm_core.channels.telegram.urllib.request.urlopen", fake_urlopen):
        TelegramSender("bot-token")(CHAT_ID, "готово")

    assert captured["url"] == "https://api.telegram.org/botbot-token/sendMessage"
    assert captured["body"] == {"chat_id": CHAT_ID, "text": "готово"}


def test_create_app_registers_senders_for_both_channels():
    settings = Settings(database_url=DB_URL, policy_path=POLICY_PATH, owner_id=OWNER_ID)
    app = create_app(settings, service_secret="test-service-secret", telegram_bot_token="tg-token")
    assert set(app.state.senders) == {"max", "telegram"}
    assert isinstance(app.state.senders["telegram"], TelegramSender)
