"""Вызов локального chief-агента Hermes (ТЗ §10.1 п.5, §10.2).

§10.2 требует «Hermes chief API server: 127.0.0.1 only, authenticated».
Такого сервера у Hermes нет — проверено живым port-scan и grep исходников
29.08.2026; его даёт плагин `max-bridge`, который поднимает листенер и
вбрасывает сообщение в тот же gateway-dispatch, которым идут Telegram-
сообщения (ADR-020). Отсюда — контракт этой стороны:

* адрес строго loopback: наружу этот порт не выходит никогда (§4.6,
  test_perimeter.py);
* подпись — тот же HMAC, что у `/internal/*` (`X-Helm-Timestamp` +
  `X-Helm-Signature` поверх тела), тем же секретом
  `hermes_service_hmac`: у Hermes он уже есть, плагин helm-control
  читает его для обратного направления;
* вызов быстрый: плагин обязан ответить сразу после приёма, не дожидаясь
  модели. Ответ chief-агента возвращается отдельным путём — через
  `/internal/outbound` в outbox (§10.3), а не в ответе на этот запрос.
  Иначе вебхук MAX висел бы на времени работы LLM и получал таймаут.
"""

from __future__ import annotations

import json
import time
import urllib.request

from .api.security import sign

DEFAULT_URL = "http://127.0.0.1:8090/v1/message"

#: Секунды. Столько нужно на приём сообщения плагином, не на работу модели.
REQUEST_TIMEOUT_SECONDS = 5


class HermesUnavailable(RuntimeError):
    """Chief-агент недоступен: задача остаётся REGISTERED (§10.3)."""


class HermesBridge:
    def __init__(self, url: str, secret: str, *,
                 timeout: int = REQUEST_TIMEOUT_SECONDS):
        self._url = url
        self._secret = secret
        self._timeout = timeout

    def deliver(self, *, task_id: str, channel: str, chat_id: str, text: str) -> None:
        body = json.dumps({
            "task_id": task_id,
            "channel": channel,
            "chat_id": chat_id,
            "text": text,
        }).encode("utf-8")
        # time.time() строкой — тот же формат метки, что проверяет
        # require_service_auth на своей стороне.
        timestamp = str(time.time())
        request = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Helm-Timestamp": timestamp,
                "X-Helm-Signature": sign(self._secret, timestamp, body),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                response.read()
        except Exception as exc:
            # Текст сообщения владельца в исключение не попадает — здесь
            # только адрес и класс ошибки.
            raise HermesUnavailable(f"{type(exc).__name__} при вызове {self._url}") from exc
