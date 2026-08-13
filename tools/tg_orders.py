#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ОКОЁМ · приём распоряжений учредителя из Telegram.

Что делает: забирает новые сообщения бота, разбирает команды, кладёт
распоряжения в state/15_ORDERS.md и отвечает учредителю, что принято и когда
будет исполнено. Ничего не исполняет сам — исполняют агенты в ближайшем цикле,
для которых очередь распоряжений стала первым, что они читают.

Почему опрос, а не вебхук. Вебхук требует постоянного адреса, который кто-то
держит; у системы такого адреса нет и заводить его ради трёх сообщений в день
— лишний узел, который будет ломаться молча. Опрос идёт из GitHub Actions по
расписанию и не требует ничего, кроме секретов, которые уже есть.

Кто может распоряжаться: только чат из TG_CHAT_ID. Имя бота публично, писать
ему может кто угодно, и без этой проверки распоряжением стало бы любое
сообщение постороннего. Чужие сообщения молча игнорируются.

Что распоряжение не может: отменить границу устава §6.1. Агенты по-прежнему
не тратят деньги, не публикуют от лица бренда, не выкатывают в прод и не
трогают персональные данные. Распоряжение, упирающееся в границу, вернётся
задачей учредителю, а не будет исполнено втихую.

Запуск:
    python3 tools/tg_orders.py            # забрать новые распоряжения
    python3 tools/tg_orders.py --dry-run  # показать, что было бы принято

Коды выхода: 0 — отработал (даже если новых сообщений не было), 1 — ошибка.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDERS_PATH = os.path.join(REPO_ROOT, "state", "15_ORDERS.md")
OFFSET_PATH = os.path.join(REPO_ROOT, "state", ".tg_offset")

API_ROOT = os.environ.get("TG_API_ROOT", "https://api.telegram.org").rstrip("/")
TIMEOUT = 30

# Длина одного распоряжения. Больше двух тысяч знаков — это не распоряжение,
# а техническое задание, и ему место в задаче бэклога, а не в чате.
MAX_ORDER_LEN = 2000

# Кому можно распорядиться. Ключ — команда, значение — как адресат называется
# в документах системы.
ADDRESSEES = {
    "борд": "БОРД",
    "board": "БОРД",
    "цпо": "CPO",
    "cpo": "CPO",
    "практика": "ПРАКТИКА",
    "записки": "ЗАПИСКИ",
    "моменты": "МОМЕНТЫ",
}

HELP = (
    "<b>Распоряжения</b>\n"
    "Команда, затем текст одним сообщением.\n\n"
    "/борд — всему борду C-1\n"
    "/цпо — CPO, портфель и приоритеты\n"
    "/практика, /записки, /моменты — product owner продукта\n\n"
    "Добавьте слово <b>срочно</b> в начало текста — распоряжение получит P0 "
    "и пойдёт в работу ближайшим циклом, не дожидаясь борда.\n\n"
    "/очередь — что принято и ещё не исполнено\n"
    "/помощь — это сообщение\n\n"
    "Пример:\n"
    "<code>/записки срочно убрать из письма входа обещание про шифрование</code>\n\n"
    "Распоряжение не отменяет границу устава: деньги, публикации от лица бренда, "
    "релизы и персональные данные остаются за вами. Такое вернётся задачей вам, "
    "а не будет сделано молча."
)


def log(message):
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


class OrdersError(Exception):
    """Ошибка, из-за которой дальше идти бессмысленно."""


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def api_call(token, method, params=None):
    url = "%s/bot%s/%s" % (API_ROOT, token, method)
    data = urllib.parse.urlencode(params or {}, encoding="utf-8").encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=data, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except ValueError:
            raise OrdersError("Telegram вернул HTTP %s: %s" % (exc.code, raw[:200]))
    except Exception as exc:
        raise OrdersError("сеть недоступна: %s" % exc)


def fetch_updates(token, offset):
    params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
    if offset is not None:
        params["offset"] = offset + 1
    payload = api_call(token, "getUpdates", params)
    if not payload.get("ok"):
        raise OrdersError("getUpdates отказал: %s" % payload.get("description", "—"))
    return payload.get("result", [])


def reply(token, chat_id, text, dry_run=False):
    if dry_run:
        log("[dry-run] ответ в чат: %s" % text.replace("\n", " ")[:160])
        return
    payload = api_call(token, "sendMessage", {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    })
    if not payload.get("ok"):
        log("ответить не удалось: %s" % payload.get("description", "—"))


# --------------------------------------------------------------------------
# Разбор команды
# --------------------------------------------------------------------------

def parse_command(text):
    """
    Сообщение → (команда, остаток текста). Команда без ведущей косой черты не
    считается командой: иначе любое слово «борд» в обычной фразе превратится в
    распоряжение.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None, stripped
    head, _, rest = stripped.partition(" ")
    command = head[1:].lower()
    # Телеграм дописывает имя бота к командам в группах: /борд@cmpas_board_bot
    command = command.split("@", 1)[0]
    return command, rest.strip()


def is_urgent(text):
    """Срочность — первым словом. В середине фразы «срочно» это просто слово."""
    return bool(re.match(r"^\s*(срочно|urgent|p0)\b", text, re.IGNORECASE))


def strip_urgency(text):
    return re.sub(r"^\s*(срочно|urgent|p0)\b[\s,:—-]*", "", text, count=1,
                  flags=re.IGNORECASE).strip()


def sanitize(text):
    """
    Текст распоряжения идёт в markdown-документ, который потом читают агенты.
    Убираем управляющие символы и обрезаем длину; разметку не трогаем — она
    попадёт в цитатный блок и структуру документа не сломает.
    """
    cleaned = "".join(ch for ch in text if ch == "\n" or ch >= " ")
    cleaned = cleaned.strip()
    if len(cleaned) > MAX_ORDER_LEN:
        cleaned = cleaned[:MAX_ORDER_LEN].rstrip() + " […обрезано]"
    return cleaned


# --------------------------------------------------------------------------
# Журнал распоряжений
# --------------------------------------------------------------------------

def read_orders():
    if not os.path.isfile(ORDERS_PATH):
        return ""
    with open(ORDERS_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def next_order_id(existing, date_compact):
    """Идентификатор O-ГГММДД-NN, нумерация сквозная внутри дня."""
    used = re.findall(r"O-%s-(\d{2})" % re.escape(date_compact), existing)
    number = max((int(x) for x in used), default=0) + 1
    return "O-%s-%02d" % (date_compact, number)


def render_order(order_id, addressee, priority, text, when, author):
    return (
        "\n#### %s · %s\n"
        "- **Адресат:** %s\n"
        "- **Принято:** %s от %s\n"
        "- **Приоритет:** %s\n"
        "- **Статус:** принято\n"
        "- **Распоряжение:**\n"
        "%s\n"
        % (order_id, first_line(text), addressee, when, author, priority,
           quote(text))
    )


def first_line(text):
    """Заголовок распоряжения — первая строка, обрезанная до читаемой длины."""
    line = text.strip().splitlines()[0] if text.strip() else "без текста"
    return line if len(line) <= 80 else line[:77].rstrip() + "…"


def quote(text):
    return "\n".join("  > " + line if line.strip() else "  >"
                     for line in text.splitlines())


def open_orders(existing):
    """Список принятых и ещё не исполненных — для ответа на /очередь."""
    found = []
    for block in existing.split("\n#### ")[1:]:
        header = block.splitlines()[0]
        status = re.search(r"\*\*Статус:\*\*\s*(\S+)", block)
        addressee = re.search(r"\*\*Адресат:\*\*\s*(\S+)", block)
        if status and status.group(1) in ("принято", "в"):
            found.append((header, addressee.group(1) if addressee else "—"))
    return found


# --------------------------------------------------------------------------
# Основной проход
# --------------------------------------------------------------------------

def read_offset():
    if not os.path.isfile(OFFSET_PATH):
        return None
    try:
        with open(OFFSET_PATH, "r", encoding="utf-8") as handle:
            return int(handle.read().strip())
    except (ValueError, OSError):
        return None


def write_offset(value):
    os.makedirs(os.path.dirname(OFFSET_PATH), exist_ok=True)
    with open(OFFSET_PATH, "w", encoding="utf-8") as handle:
        handle.write("%d\n" % value)


def process(token, chat_id, now, dry_run=False):
    updates = fetch_updates(token, read_offset())
    if not updates:
        log("новых сообщений нет.")
        return 0

    existing = read_orders()
    additions = []
    highest = None
    accepted = 0

    for update in updates:
        highest = update.get("update_id", highest)
        message = update.get("message") or {}
        text = message.get("text") or ""
        sender_chat = str((message.get("chat") or {}).get("id", ""))

        # Распоряжаться может только учредитель. Остальным не отвечаем вовсе:
        # ответ подтверждает, что бот жив, и приглашает пробовать дальше.
        if sender_chat != str(chat_id):
            log("сообщение из чужого чата %s — игнорируем." % sender_chat)
            continue
        if not text.strip():
            continue

        command, rest = parse_command(text)
        author = (message.get("from") or {}).get("first_name") or "учредитель"

        if command in (None, "start", "помощь", "help"):
            reply(token, chat_id, HELP, dry_run)
            continue

        if command in ("очередь", "queue"):
            items = open_orders(existing + "".join(additions))
            if items:
                body = "\n".join("• %s — %s" % (a, h) for h, a in items[:15])
                reply(token, chat_id, "<b>Открытые распоряжения</b>\n" + body, dry_run)
            else:
                reply(token, chat_id, "Открытых распоряжений нет.", dry_run)
            continue

        if command not in ADDRESSEES:
            reply(token, chat_id,
                  "Не знаю команду <code>/%s</code>. /помощь — список." % command,
                  dry_run)
            continue

        if not rest.strip():
            reply(token, chat_id,
                  "После <code>/%s</code> нужен текст распоряжения." % command, dry_run)
            continue

        urgent = is_urgent(rest)
        body = sanitize(strip_urgency(rest) if urgent else rest)
        addressee = ADDRESSEES[command]
        order_id = next_order_id(existing + "".join(additions), now["compact"])
        priority = "P0 · срочно" if urgent else "обычный"

        additions.append(render_order(order_id, addressee, priority, body,
                                      now["human"], author))
        accepted += 1

        when = ("ближайшим ночным циклом" if urgent else
                "ближайшим ночным циклом" if addressee != "БОРД" else
                "ближайшим бордом")
        reply(token, chat_id,
              "<b>%s принято</b>\nАдресат: %s\nПриоритет: %s\nВ работу: %s\n\n"
              "<i>Если упрётся в границу устава — вернётся задачей вам "
              "с вариантами.</i>" % (order_id, addressee, priority, when),
              dry_run)

    if highest is not None and not dry_run:
        write_offset(highest)

    if additions:
        if dry_run:
            log("[dry-run] в 15_ORDERS.md добавилось бы:\n%s" % "".join(additions))
        else:
            append_orders(additions)
        log("принято распоряжений: %d" % accepted)
    else:
        log("распоряжений среди сообщений не было.")
    return 0


def append_orders(additions):
    """Новые распоряжения — сверху журнала, под заголовком «Открытые»."""
    existing = read_orders()
    if not existing:
        raise OrdersError("нет файла %s — журнал распоряжений должен лежать в "
                          "репозитории" % ORDERS_PATH)
    marker = "## Открытые"
    if marker not in existing:
        raise OrdersError("в %s нет раздела «## Открытые»" % ORDERS_PATH)
    head, _, tail = existing.partition(marker)
    # Заглушка пустого раздела уезжает вместе с первым распоряжением, иначе
    # «_пусто_» остаётся висеть под списком непустых распоряжений.
    tail = re.sub(r"\A\s*\n_пусто_\n", "\n", tail, count=1)
    updated = head + marker + "".join(additions) + tail
    with open(ORDERS_PATH, "w", encoding="utf-8") as handle:
        handle.write(updated)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tg_orders.py",
        description="ОКОЁМ · приём распоряжений учредителя из Telegram.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="показать, что было бы принято, ничего не записывая")
    parser.add_argument("--now", default=None,
                        help="время приёма в виде ГГГГ-ММ-ДД ЧЧ:ММ (по умолчанию — системное)")
    args = parser.parse_args(argv)

    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        log("ОШИБКА: нужны TG_BOT_TOKEN и TG_CHAT_ID. Проверка канала — "
            "python3 tools/tg_doctor.py")
        return 1

    stamp = args.now
    if not stamp:
        import datetime
        stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    now = {"human": stamp + " UTC", "compact": stamp[2:10].replace("-", "")}

    try:
        return process(token, chat_id, now, dry_run=args.dry_run)
    except OrdersError as exc:
        log("ОШИБКА: %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
