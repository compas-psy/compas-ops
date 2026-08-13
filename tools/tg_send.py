#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ОКОЁМ · доставка артефактов борда в Telegram.

Отправка текста, документов и картинок в Telegram через Bot API (бот @cmpas_board_bot).
Только стандартная библиотека Python: контейнер может не иметь доступа к pip.

Переменные окружения:
    TG_BOT_TOKEN — токен бота от @BotFather (обязательно);
    TG_CHAT_ID   — идентификатор чата учредителя (обязательно, если не задан --chat-id).

Подкоманды:
    text  — отправить сообщение (parse_mode=HTML, авто-нарезка по 4096 символов);
    file  — отправить документ с подписью (дек борда, выгрузка);
    photo — отправить картинку с подписью.

Коды выхода: 0 — всё отправлено, 1 — ошибка (осмысленное сообщение в stderr).
"""

import argparse
import html
import json
import mimetypes
import os
import random
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# Адрес API можно подменить через TG_API_ROOT: для зеркала, если api.telegram.org
# недоступен из контейнера, и для локальных проверок без сети.
API_ROOT = os.environ.get("TG_API_ROOT", "https://api.telegram.org").rstrip("/")

# Лимиты Telegram Bot API.
TEXT_LIMIT = 4096      # максимум символов в одном сообщении
CAPTION_LIMIT = 1024   # максимум символов в подписи к файлу или фото

# Политика повторов: сетевые сбои, 5xx и 429 (с уважением к retry_after).
MAX_ATTEMPTS = 5
BASE_BACKOFF = 1.5     # секунды, растёт экспоненциально
MAX_BACKOFF = 30.0
HTTP_TIMEOUT = 60.0


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------

def log(message):
    """Служебный вывод — всегда в stderr, чтобы stdout оставался машиночитаемым."""
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


class SendError(Exception):
    """Ошибка отправки, которую уже незачем повторять."""


def esc(text):
    """Экранирование пользовательского текста под parse_mode=HTML."""
    return html.escape(text, quote=False)


def get_credentials(args):
    """Токен и chat_id из окружения (или из аргументов) с понятной ошибкой."""
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = (args.chat_id or os.environ.get("TG_CHAT_ID", "")).strip()

    missing = []
    if not token:
        missing.append("TG_BOT_TOKEN")
    if not chat_id:
        missing.append("TG_CHAT_ID")
    if missing:
        raise SendError(
            "не заданы переменные окружения: " + ", ".join(missing) + ".\n"
            "Задайте их перед запуском, например:\n"
            "  export TG_BOT_TOKEN='123456:AA...'   # токен бота @cmpas_board_bot\n"
            "  export TG_CHAT_ID='-1001234567890'   # чат учредителя\n"
            "Секреты в код и в проект не кладём (см. 06_DELIVERY.md)."
        )
    return token, chat_id


def split_text(text, limit=TEXT_LIMIT):
    """
    Нарезка длинного текста на части не длиннее limit символов.

    Режем по границам строк: сначала по пустой строке (абзац), затем по любому
    переносу. Если одна строка длиннее лимита — режем её по словам, а слово
    длиннее лимита — жёстко по символам. Порядок и содержимое сохраняются.
    """
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    parts = []
    buf = ""

    def flush():
        nonlocal buf
        if buf.strip():
            parts.append(buf.rstrip("\n"))
        buf = ""

    for line in text.split("\n"):
        # Слишком длинная одиночная строка — дробим её отдельно.
        if len(line) > limit:
            flush()
            parts.extend(_split_long_line(line, limit))
            continue
        candidate = line if not buf else buf + "\n" + line
        if len(candidate) > limit:
            flush()
            buf = line
        else:
            buf = candidate
    flush()
    return parts


def _split_long_line(line, limit):
    """Дробление одной сверхдлинной строки: сперва по словам, потом по символам."""
    chunks = []
    buf = ""
    for word in line.split(" "):
        while len(word) > limit:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(word[:limit])
            word = word[limit:]
        candidate = word if not buf else buf + " " + word
        if len(candidate) > limit:
            chunks.append(buf)
            buf = word
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks


def clip_caption(caption):
    """Подпись к файлу ограничена 1024 символами — обрезаем аккуратно, с многоточием."""
    if caption is None:
        return None
    if len(caption) <= CAPTION_LIMIT:
        return caption
    return caption[: CAPTION_LIMIT - 1].rstrip() + "…"


# --------------------------------------------------------------------------
# Транспорт
# --------------------------------------------------------------------------

def _ssl_context():
    """Контекст TLS: уважаем корпоративный CA-бандл, если он задан в окружении."""
    ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if ca and os.path.exists(ca):
        return ssl.create_default_context(cafile=ca)
    return ssl.create_default_context()


def _encode_multipart(fields, files):
    """
    Сборка тела multipart/form-data вручную (без requests).

    fields — словарь текстовых полей (кириллица кодируется в UTF-8),
    files  — список кортежей (имя_поля, имя_файла, байты).
    """
    boundary = "----OkoemBoundary" + uuid.uuid4().hex
    crlf = b"\r\n"
    body = bytearray()

    for name, value in fields.items():
        if value is None:
            continue
        body += b"--" + boundary.encode() + crlf
        body += ('Content-Disposition: form-data; name="%s"' % name).encode("utf-8") + crlf + crlf
        body += str(value).encode("utf-8") + crlf

    for name, filename, payload in files:
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body += b"--" + boundary.encode() + crlf
        disp = 'Content-Disposition: form-data; name="%s"; filename="%s"' % (name, filename)
        body += disp.encode("utf-8") + crlf
        body += ("Content-Type: %s" % ctype).encode("utf-8") + crlf + crlf
        body += payload + crlf

    body += b"--" + boundary.encode() + b"--" + crlf
    return bytes(body), "multipart/form-data; boundary=" + boundary


def api_call(token, method, fields, files=None, dry_run=False):
    """
    Вызов метода Bot API с повторами.

    Повторяем: сетевые ошибки, таймауты, 5xx и 429 (пауза = retry_after + запас).
    Не повторяем: 4xx кроме 429 — это ошибка в данных, повтор не поможет.
    """
    if dry_run:
        preview = {k: (v if len(str(v)) < 200 else str(v)[:200] + "…") for k, v in fields.items()}
        log("[dry-run] %s <- %s" % (method, json.dumps(preview, ensure_ascii=False)))
        for name, filename, payload in (files or []):
            log("[dry-run]   файл %s: %s (%d байт)" % (name, filename, len(payload)))
        return {"ok": True, "result": {"message_id": 0, "dry_run": True}}

    url = "%s/bot%s/%s" % (API_ROOT, token, method)
    ctx = _ssl_context()
    last_error = "неизвестная ошибка"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if files:
            data, content_type = _encode_multipart(fields, files)
        else:
            clean = {k: v for k, v in fields.items() if v is not None}
            data = urllib.parse.urlencode(clean, encoding="utf-8").encode("utf-8")
            content_type = "application/x-www-form-urlencoded"

        request = urllib.request.Request(url, data=data, method="POST")
        request.add_header("Content-Type", content_type)
        request.add_header("User-Agent", "okoem-tg-send/1.0")

        pause = None
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT, context=ctx) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if payload.get("ok"):
                    return payload
                last_error = "Telegram отказал: %s" % payload.get("description", payload)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {}
            description = payload.get("description", raw.strip()[:300] or exc.reason)
            if exc.code == 429:
                retry_after = int(payload.get("parameters", {}).get("retry_after", 5))
                pause = retry_after + 1
                last_error = "429 Too Many Requests (retry_after=%ss)" % retry_after
            elif 500 <= exc.code < 600:
                last_error = "HTTP %s: %s" % (exc.code, description)
            else:
                # 400/401/403/404 — данные или доступ. Повтор бессмыслен.
                raise SendError(
                    "Telegram вернул HTTP %s: %s\n"
                    "Проверьте TG_BOT_TOKEN, TG_CHAT_ID и то, что учредитель нажал /start у бота."
                    % (exc.code, description)
                )
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = "сеть недоступна: %s" % exc

        if attempt == MAX_ATTEMPTS:
            break
        if pause is None:
            pause = min(BASE_BACKOFF * (2 ** (attempt - 1)), MAX_BACKOFF)
            pause += random.uniform(0, 0.4)  # джиттер, чтобы не биться в стену синхронно
        log("попытка %d/%d не удалась (%s), пауза %.1f с" % (attempt, MAX_ATTEMPTS, last_error, pause))
        time.sleep(pause)

    raise SendError("не удалось выполнить %s за %d попыток. Последняя ошибка: %s"
                    % (method, MAX_ATTEMPTS, last_error))


# --------------------------------------------------------------------------
# Подкоманды
# --------------------------------------------------------------------------

def read_input_text(args):
    """Текст из аргумента, из файла (--from-file) или из stdin (аргумент '-')."""
    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as handle:
            return handle.read()
    if args.text is None or args.text == "-":
        return sys.stdin.read()
    return args.text


def cmd_text(args, token, chat_id):
    raw = read_input_text(args).strip("\n")
    if not raw.strip():
        raise SendError("пустой текст: отправлять нечего.")

    # По умолчанию считаем вход небезопасным и экранируем.
    # Готовые шаблоны с разметкой (<b>, <i>, <code>) отправляем с флагом --html.
    body = raw if args.html else esc(raw)

    parts = split_text(body, TEXT_LIMIT)
    total = len(parts)
    for index, part in enumerate(parts, start=1):
        fields = {
            "chat_id": chat_id,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true" if args.no_preview else None,
            "disable_notification": "true" if args.silent else None,
        }
        api_call(token, "sendMessage", fields, dry_run=args.dry_run)
        if total > 1:
            log("отправлена часть %d из %d (%d символов)" % (index, total, len(part)))
            if index < total:
                time.sleep(0.4)  # мягкий темп, чтобы не поймать 429 на длинной сводке
    log("готово: сообщение отправлено (%d %s)" % (total, "часть" if total == 1 else "частей"))
    return 0


def _send_attachment(args, token, chat_id, method, field_name, human_name):
    path = args.path
    if not os.path.isfile(path):
        raise SendError("файл не найден: %s" % path)
    size = os.path.getsize(path)
    if size == 0:
        raise SendError("файл пустой: %s" % path)
    limit = 50 * 1024 * 1024 if method == "sendDocument" else 10 * 1024 * 1024
    if size > limit:
        raise SendError("%s слишком велик: %.1f МБ при лимите %d МБ"
                        % (human_name, size / 1048576.0, limit // 1048576))

    with open(path, "rb") as handle:
        payload = handle.read()

    caption = args.caption
    if caption and not args.html:
        caption = esc(caption)
    caption = clip_caption(caption)

    fields = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "HTML" if caption else None,
        "disable_notification": "true" if args.silent else None,
    }
    files = [(field_name, os.path.basename(path), payload)]
    api_call(token, method, fields, files=files, dry_run=args.dry_run)
    log("готово: %s отправлен (%s, %.1f КБ)" % (human_name, os.path.basename(path), size / 1024.0))
    return 0


def cmd_file(args, token, chat_id):
    return _send_attachment(args, token, chat_id, "sendDocument", "document", "документ")


def cmd_photo(args, token, chat_id):
    return _send_attachment(args, token, chat_id, "sendPhoto", "photo", "снимок")


# --------------------------------------------------------------------------
# Точка входа
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="tg_send.py",
        description="ОКОЁМ · отправка артефактов борда в Telegram (только stdlib).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  tg_send.py text 'Ночной цикл завершён'\n"
            "  cat summary.html | tg_send.py text - --html\n"
            "  tg_send.py file /root/ops/board_demo.html --caption 'Дек борда C-1 № 14'\n"
            "  tg_send.py photo /root/ops/slide1.png --caption 'Первый слайд'\n"
        ),
    )
    parser.add_argument("--chat-id", default=None, help="перебить TG_CHAT_ID для одного запуска")
    parser.add_argument("--silent", action="store_true", help="доставить без звука")
    parser.add_argument("--dry-run", action="store_true",
                        help="ничего не отправлять, показать что ушло бы (проверка без сети)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_text = subparsers.add_parser("text", help="отправить сообщение")
    p_text.add_argument("text", nargs="?", default=None, help="текст; '-' или пусто — читать из stdin")
    p_text.add_argument("--from-file", default=None, help="прочитать текст из файла")
    p_text.add_argument("--html", action="store_true",
                        help="вход уже содержит разметку HTML (не экранировать)")
    p_text.add_argument("--no-preview", action="store_true", help="без превью ссылок")
    p_text.set_defaults(func=cmd_text)

    p_file = subparsers.add_parser("file", help="отправить документ")
    p_file.add_argument("path", help="путь к файлу")
    p_file.add_argument("--caption", default=None, help="подпись")
    p_file.add_argument("--html", action="store_true", help="подпись уже содержит разметку HTML")
    p_file.set_defaults(func=cmd_file)

    p_photo = subparsers.add_parser("photo", help="отправить картинку")
    p_photo.add_argument("path", help="путь к картинке")
    p_photo.add_argument("--caption", default=None, help="подпись")
    p_photo.add_argument("--html", action="store_true", help="подпись уже содержит разметку HTML")
    p_photo.set_defaults(func=cmd_photo)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.dry_run:
            token = os.environ.get("TG_BOT_TOKEN", "dry-run-token")
            chat_id = args.chat_id or os.environ.get("TG_CHAT_ID", "dry-run-chat")
        else:
            token, chat_id = get_credentials(args)
        return args.func(args, token, chat_id)
    except SendError as exc:
        log("ОШИБКА: %s" % exc)
        return 1
    except KeyboardInterrupt:
        log("прервано пользователем")
        return 1
    except Exception as exc:  # последний рубеж: наружу не должно улететь трейсбека
        log("ОШИБКА: непредвиденный сбой (%s): %s" % (type(exc).__name__, exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
