"""helm-control — Control Plane gate до LLM-вызова (ТЗ §9.3).

pre_gateway_dispatch регистрирует входящее сообщение в Control Plane ДО
того, как оно дойдёт до LLM. Если Control Plane не подтверждает
регистрацию (недоступен, отверг подпись, отверг owner_id) — сообщение до
модели не доходит: это fail-closed по конструкции, не по доп. проверке,
потому что LLM вызывается уже ПОСЛЕ этой функции, а не внутри неё.

pre_llm_call передаёт HELM_TASK_ID зарегистрированной задачи в модель
коротким контекстом (§9.3: "короткий trusted context").

Секрет HMAC читается из того же docker secret, что видит helm-core
(/run/secrets/hermes_service_hmac на хосте, где эта обвязка запущена
вне контейнера — путь совпадает, т.к. Hermes работает на хосте, а не
в Docker, и секреты лежат в /etc/helm/secrets; при переносе Hermes в
контейнер этот путь придётся поменять на /run/secrets, как уже было
найдено для helm-core).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.request

CONTROL_PLANE_URL = "http://127.0.0.1:8080/internal/inbound"
HMAC_SECRET_PATH = "/etc/helm/secrets/hermes_service_hmac"
REQUEST_TIMEOUT = 5

#: Живёт только в памяти процесса гейтвея — переживает один запуск, не
#: рестарт. Смысл этого кэша — донести task_id из pre_gateway_dispatch до
#: pre_llm_call в рамках той же обработки сообщения, не более.
_task_ids: dict[str, str] = {}


def _read_secret() -> str:
    with open(HMAC_SECRET_PATH, encoding="utf-8") as f:
        return f.read().strip()


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    material = timestamp.encode("utf-8") + b"\x00" + body
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


def _register_task(channel: str, external_message_id: str, owner_id: str, text: str) -> dict:
    body_obj = {
        "channel": channel,
        "external_message_id": external_message_id,
        "owner_id": owner_id,
        "text": text,
    }
    body = json.dumps(body_obj).encode("utf-8")
    ts = str(time.time())
    sig = _sign(_read_secret(), ts, body)
    req = urllib.request.Request(
        CONTROL_PLANE_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Helm-Timestamp": ts,
            "X-Helm-Signature": sig,
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


async def handle(event=None, gateway=None, session_store=None, **kwargs):
    # pre_gateway_dispatch передаёт event+gateway; pre_llm_call — нет.
    if event is not None and gateway is not None:
        return await _on_pre_gateway_dispatch(event, gateway)
    return _on_pre_llm_call(kwargs)


async def _on_pre_gateway_dispatch(event, gateway):
    if not event.text:
        return None

    source = event.source
    channel = source.platform.value if source and source.platform else "system"
    owner_id = str(event.user_id) if event.user_id is not None else ""
    external_message_id = event.message_id or (
        channel + ":" + owner_id + ":" + str(time.time())
    )

    try:
        result = _register_task(channel, external_message_id, owner_id, event.text)
    except Exception as exc:
        try:
            await gateway.adapters[source.platform].send(
                text="HELM Control Plane недоступен. Задача не запущена.",
                chat_id=source.chat_id,
            )
        except Exception:
            pass
        return {"action": "skip", "reason": "control_plane_unavailable: " + str(exc)}

    if source and source.chat_id:
        _task_ids[str(source.chat_id)] = result["task_id"]
    return None


def _on_pre_llm_call(kwargs: dict):
    session_id = kwargs.get("session_id")
    task_id = _task_ids.get(str(session_id)) if session_id is not None else None
    if not task_id:
        return None
    return {"context": "HELM_TASK_ID=" + task_id}
