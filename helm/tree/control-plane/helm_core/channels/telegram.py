"""Канал Telegram, исходящая часть доставщика outbox (§10.3).

Обычные ответы владельцу по-прежнему уходят синхронно через собственный
адаптер Hermes (`_send_reply()` в `helm-control`) — этот файл их не
заменяет и не дублирует. Он нужен ровно для одного случая: НАЙДЕНО
ЖИВЬЁМ 30.08.2026 (первый живой тест Telegram-стороны P8.5.7) — уведомление
о завершении разбора файла кладёт в `outbox` `helm-knowledge-worker`,
отдельный контейнер без какой-либо связи с процессом Hermes gateway;
`app.state.senders` знал только про `"max"`, доставщик находил
`sender is None` для канала `"telegram"` и сразу помечал сообщение
`FAILED` без единой попытки — молча, без исключения в логе. Официальный
Telegram Bot API `sendMessage` — обычный stateless POST, безопасно
вызывать из нескольких процессов одним и тем же токеном одновременно
(в отличие от `getUpdates`/long polling, который требует единственного
потребителя) — второй "живой" отправитель тем же ботом ничего не ломает.
"""

from __future__ import annotations

import json
import urllib.request

API_BASE = "https://api.telegram.org"

REQUEST_TIMEOUT_SECONDS = 10


def _send_request(token: str, chat_id: str, text: str, timeout: int) -> None:
    body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}/bot{token}/sendMessage",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


class TelegramSender:
    """Отправка в Telegram. Вызывается доставщиком outbox (§10.3).

    Класс, а не функция — тот же повод, что у `MaxSender`: доставщику
    нужен вызываемый объект, уже знающий токен.
    """

    channel = "telegram"

    def __init__(self, token: str, *, timeout: int = REQUEST_TIMEOUT_SECONDS):
        self._token = token
        self._timeout = timeout

    def __call__(self, recipient: str, text: str) -> None:
        if not self._token:
            raise RuntimeError("токен бота Telegram не задан — отправка невозможна")
        _send_request(self._token, recipient, text, self._timeout)
