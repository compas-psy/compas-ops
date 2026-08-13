#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ОКОЁМ · диагностика канала доставки в Telegram.

Отвечает на три вопроса по порядку, и на каждом останавливается, если ответ
плохой: жив ли токен, известен ли чат, доходит ли сообщение. Когда бот молчит,
причина всегда одна из этих трёх, и гадать не нужно.

Токен не печатается ни при каком исходе, включая ошибки: в лог диагностики
смотрят, когда что-то сломалось, и такой лог легко переслать целиком.

Запуск:
    python3 tools/tg_doctor.py            # проверить токен и чат
    python3 tools/tg_doctor.py --send     # плюс отправить проверочное сообщение

Коды выхода: 0 — канал исправен, 1 — нет (что делать, написано в выводе).
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = os.environ.get("TG_API_ROOT", "https://api.telegram.org").rstrip("/")
TIMEOUT = 30

# Форма токена BotFather: <id бота>:<секрет>. Проверяем форму, не значение —
# это ловит самую частую поломку, лишний пробел или перенос строки при копировании.
TOKEN_SHAPE = re.compile(r"^[0-9]{6,}:[A-Za-z0-9_-]{30,}$")


def out(message):
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def fail(message, hint=None):
    sys.stdout.write("ОШИБКА: " + message + "\n")
    if hint:
        sys.stdout.write("  → " + hint.replace("\n", "\n    ") + "\n")
    sys.stdout.flush()
    return 1


def call(token, method, params=None):
    """Вызов Bot API. Возвращает (payload, http_code|None). Токен наружу не отдаём."""
    url = "%s/bot%s/%s" % (API_ROOT, token, method)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            return json.load(response), 200
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(raw), exc.code
        except ValueError:
            return {"description": raw.strip()[:300]}, exc.code
    except Exception as exc:  # сеть, DNS, TLS
        return {"description": "сеть недоступна: %s" % exc}, None


def check_token(token):
    """Шаг 1: токен есть, по форме верный, и Telegram его принимает."""
    if not token:
        return fail(
            "переменная TG_BOT_TOKEN не задана.",
            "В GitHub Actions: Settings → Secrets and variables → Actions →\n"
            "New repository secret → имя TG_BOT_TOKEN.\n"
            "В облачной среде Claude: настройки среды → Environment variables.",
        )
    if not TOKEN_SHAPE.match(token):
        return fail(
            "TG_BOT_TOKEN не похож на токен BotFather.",
            "Ожидается вид 123456789:AA... — проверьте, не скопирован ли токен\n"
            "с пробелом, кавычками или переносом строки.",
        )

    data, code = call(token, "getMe")
    if not data.get("ok"):
        description = data.get("description", "неизвестная ошибка")
        if code == 401:
            return fail(
                "Telegram отклонил токен (401 Unauthorized).",
                "Токен отозван или неверен. Возьмите новый: @BotFather → /mybots →\n"
                "выберите бота → API Token, и обновите секрет TG_BOT_TOKEN.\n"
                "Если токен когда-то попадал в переписку — сначала /revoke.",
            )
        if code is None:
            return fail(
                "не достучались до api.telegram.org: %s" % description,
                "Так выглядит среда без выхода в интернет. Либо разрешите домен\n"
                "api.telegram.org в сетевых настройках среды, либо доставляйте\n"
                "через GitHub Actions — см. charter/09_COWORK.md.",
            )
        return fail("Telegram отклонил токен: %s" % description)

    bot = data.get("result", {})
    out("1/3 Токен рабочий. Бот: @%s (%s), id %s"
        % (bot.get("username"), bot.get("first_name"), bot.get("id")))
    return 0


def check_chat(token, chat_id):
    """Шаг 2: чат известен и доступен; если нет — подсказываем его id."""
    if chat_id:
        data, _ = call(token, "getChat", {"chat_id": chat_id})
        if data.get("ok"):
            chat = data["result"]
            name = chat.get("title") or " ".join(
                x for x in (chat.get("first_name"), chat.get("last_name")) if x)
            out("2/3 Чат найден: %s — %s (%s)" % (chat_id, name or "—", chat.get("type")))
            return 0

        description = data.get("description", "")
        hint = ("Частые причины: учредитель ни разу не написал боту (бот не может\n"
                "начать диалог первым); chat_id взят от другого бота; для группы\n"
                "потерян знак минус в начале id.")
        return fail("TG_CHAT_ID задан (%s), но Telegram его не принял: %s"
                    % (chat_id, description), hint)

    # chat_id неизвестен — вытаскиваем из входящих сообщений бота.
    out("2/3 TG_CHAT_ID не задан. Ищем чат в getUpdates…")
    data, _ = call(token, "getUpdates", {"limit": 20})
    found = {}
    for update in data.get("result", []):
        message = (update.get("message") or update.get("channel_post")
                   or update.get("edited_message") or {})
        chat = message.get("chat") or {}
        if chat.get("id") is not None:
            label = chat.get("title") or " ".join(
                x for x in (chat.get("first_name"), chat.get("last_name"),
                            chat.get("username")) if x)
            found[chat["id"]] = "%s (%s)" % (label or "—", chat.get("type"))

    if not found:
        return fail(
            "бот не получил ни одного сообщения, взять chat_id неоткуда.",
            "Откройте бота в Telegram, нажмите Start, напишите ему любое слово\n"
            "и запустите диагностику снова.",
        )

    out("  Найдены чаты — нужный id положите в TG_CHAT_ID:")
    for chat_id_found, label in found.items():
        out("      %s  —  %s" % (chat_id_found, label))
    return fail("TG_CHAT_ID не задан.",
                "Возьмите id из списка выше и создайте секрет TG_CHAT_ID.")


def send_test(token, chat_id):
    """Шаг 3: контрольная отправка — единственное настоящее доказательство."""
    text = ("<b>Проверка канала доставки</b>\n"
            "Канал работает: текст доходит. Так же будут приходить сводка ночного "
            "цикла и сообщение борда, а следом — презентация файлом.")
    data, _ = call(token, "sendMessage", {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    })
    if not data.get("ok"):
        return fail("сообщение не ушло: %s" % data.get("description", "—"))
    out("3/3 Проверочное сообщение отправлено.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tg_doctor.py",
        description="ОКОЁМ · проверка канала доставки в Telegram.",
    )
    parser.add_argument("--send", action="store_true",
                        help="отправить проверочное сообщение в чат")
    args = parser.parse_args(argv)

    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()

    code = check_token(token)
    if code:
        return code
    code = check_chat(token, chat_id)
    if code:
        return code
    if args.send:
        code = send_test(token, chat_id)
        if code:
            return code
    else:
        out("3/3 Отправка не проверялась (запустите с --send).")

    out("Канал исправен.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
