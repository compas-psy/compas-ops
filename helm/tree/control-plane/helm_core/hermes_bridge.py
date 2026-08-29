"""Вызов chief-агента Hermes через его штатный API (ТЗ §10.1 п.5, §10.2).

Ранее (29.08.2026, первая версия этого файла) считалось, что «Hermes
chief API server» из §10.2 не существует — вывод получен grep'ом по
терминологии САМОЙ СПЕКИ («chief api», «named conversation») по
исходникам Hermes: этих слов там действительно нет. Вывод был ошибочным
не по факту (порт не слушал — это было правдой), а по причине: у Hermes
уже есть полноценный встроенный OpenAI-совместимый API-сервер
(`gateway/platforms/api_server.py`, `Platform.API_SERVER`), просто
выключенный (не задан `API_SERVER_KEY`). `POST /v1/responses` с полем
`conversation` — это и есть «Hermes Responses API с named conversation»
из §10.2, один в один, только под именами из OpenAI Responses API, а не
из текста спеки. Найдено разведкой по исходникам на сервере, вторым
заходом. Больше не нужен ни отдельный Hermes-side плагин, ни
регистрация новой платформы — Control Plane становится обычным HTTP-
клиентом уже существующего, протестированного API.

Контракт (§10.2, проверено по исходникам `gateway/platforms/api_server.py`,
живой смоук — `scripts/hermes-responses-diagnose.sh`):

* `http://host.docker.internal:8642/v1/responses`, НЕ `127.0.0.1`.
  НАЙДЕНО ЖИВЬЁМ 29.08.2026: Hermes работает на хосте, Control Plane —
  в Docker-контейнере со своим сетевым namespace; `127.0.0.1` внутри
  контейнера означает сам контейнер, а сокет Hermes, привязанный строго
  к `127.0.0.1` хоста, физически не примет пакет с другого интерфейса
  (докер-мост) — это ограничение ядра на уровне сокета, не файрвола.
  Поэтому Hermes теперь слушает `0.0.0.0`, а хостовый `ufw` пропускает
  8642 ТОЛЬКО из подсети докер-моста (`172.18.0.0/16`) — публичный
  интернет по-прежнему видит только 22/80/443 (`default deny incoming`).
  `host.docker.internal` резолвится через `extra_hosts: host-gateway`
  в docker-compose.yml — не хардкодит IP моста, который может смениться
  при пересоздании сети;
* `Authorization: Bearer <API_SERVER_KEY>` (тот же ключ, что в
  `~/.hermes/.env`, — секрет `hermes_api_server_key`, тот же формат
  Bearer, что и у остальных маршрутов этого API-сервера);
* тело `{"model": "hermes-agent", "input": text, "conversation": name}`
  — `conversation` резолвится сервером в `previous_response_id`
  последнего ответа этого имени сам; ничего, кроме имени, Control
  Plane не хранит — multi-turn context живёт целиком внутри Hermes
  (§10.2: «без хранения полного MAX transcript в Control Plane»).

Вызов СИНХРОННЫЙ и может идти столько же, сколько ход агента (минуты,
если использует инструменты) — `_handle_responses` в api_server.py
делает `await self._run_agent(...)` внутри самого HTTP-запроса, ответ
не стримится. Поэтому `deliver()` вызывается вызывающим (`hooks.py`)
из фоновой задачи, а не в теле обработчика вебхука — тот обязан
ответить MAX сразу (см. предыдущую версию этого файла и её собственный
комментарий про 5-секундный таймаут, верный по духу, но написанный под
несуществующий на тот момент листенер).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_URL = "http://host.docker.internal:8642/v1/responses"

#: Секунды. Реальный ход агента, не быстрый ACK — минуты, если агент
#: пользуется инструментами. Слишком короткий таймаут здесь означал бы
#: ложный HermesUnavailable на каждом чуть более сложном ответе.
REQUEST_TIMEOUT_SECONDS = 180


class HermesUnavailable(RuntimeError):
    """Chief-агент недоступен или ответил ошибкой (§10.3)."""


def conversation_name(owner_id: str) -> str:
    """Имя named conversation для MAX (§10.2: `conversation = "helm-max-owner"`).

    Отдельное от Telegram: Telegram общается с Hermes через собственный
    нативный адаптер и свою сессию — эта функция создаёт ВТОРОЙ, MAX-
    only разговор с тем же chief-агентом, не пытаясь слить историю двух
    каналов в одну (§10.2 говорит про multi-turn ВНУТРИ MAX, не про
    объединение с Telegram).
    """
    return f"helm-max-{owner_id}"


class HermesBridge:
    def __init__(self, url: str, api_key: str, *,
                 timeout: int = REQUEST_TIMEOUT_SECONDS):
        self._url = url
        self._api_key = api_key
        self._timeout = timeout

    def deliver(self, *, owner_id: str, text: str) -> str:
        """Прогнать текст через chief-агента. Возвращает текст его ответа."""
        if not self._api_key:
            raise HermesUnavailable("API_SERVER_KEY не задан — деплой не завершён")

        body = json.dumps({
            "model": "hermes-agent",
            "input": text,
            "conversation": conversation_name(owner_id),
        }).encode("utf-8")
        request = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # Тело ответа не логируется целиком у вызывающего — сюда,
            # в исключение, попадает: message-текст владельца в теле
            # ответа Hermes не эхуется (это не MAX), риска утечки нет.
            raise HermesUnavailable(
                f"HTTP {exc.code} от Hermes: {exc.read().decode(errors='replace')}"
            ) from exc
        except Exception as exc:
            raise HermesUnavailable(f"{type(exc).__name__} при вызове {self._url}") from exc

        return _extract_reply_text(payload)


def _extract_reply_text(payload: dict) -> str:
    """Достать текст ответа из тела OpenAI Responses API.

    Формат (`output: [{type: "message", content: [{type: "output_text",
    text: ...}]}]`) — стандартный для Responses API; здесь не
    предполагается вслепую, а проверяется явно, с понятной ошибкой при
    расхождении, а не молчаливым KeyError/IndexError где-то внутри.
    """
    output = payload.get("output")
    if not isinstance(output, list):
        raise HermesUnavailable(f"нет поля 'output' в ответе Hermes: {sorted(payload)}")
    for item in output:
        if item.get("type") != "message":
            continue
        for chunk in item.get("content", []):
            if chunk.get("type") == "output_text" and chunk.get("text"):
                return chunk["text"]
    raise HermesUnavailable("в ответе Hermes нет текстового message-элемента")
