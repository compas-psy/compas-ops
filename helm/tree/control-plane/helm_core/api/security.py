"""Аутентификация служебных вызовов и заголовки безопасности (§30.7, §30.9)."""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import Header, HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware

#: Окно приёма подписи. Слишком широкое окно превращает перехваченный
#: запрос в бессрочный; слишком узкое ломается на расхождении часов.
HMAC_WINDOW_SECONDS = 300


def sign(secret: str, timestamp: str, body: bytes) -> str:
    material = timestamp.encode("utf-8") + b"\x00" + body
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


async def require_service_auth(
    request: Request,
    x_helm_timestamp: str = Header(...),
    x_helm_signature: str = Header(...),
) -> None:
    """HMAC для internal API (§7.3): «Все internal endpoints требуют service auth».

    Подписывается тело вместе с меткой времени. Без метки внутри подписи
    перехваченный запрос можно было бы переиграть спустя сутки.
    """
    secret = request.app.state.service_secret
    try:
        skew = abs(time.time() - float(x_helm_timestamp))
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "некорректная метка времени")
    if skew > HMAC_WINDOW_SECONDS:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "метка времени вне окна")

    body = await request.body()
    expected = sign(secret, x_helm_timestamp, body)
    # compare_digest, а не ==: сравнение строк выходит по первому различию и
    # утекает подпись по времени ответа.
    if not hmac.compare_digest(expected, x_helm_signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "подпись не совпадает")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """§30.7: «API has no-store; CSP/frame/referrer headers pass check»."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/api/", "/auth/", "/internal/")):
            # Ответы API не кэшируются нигде: в них состояние одобрений и
            # суммы, и показать вчерашние — хуже, чем не показать ничего.
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=(), payment=()"
        )
        return response
