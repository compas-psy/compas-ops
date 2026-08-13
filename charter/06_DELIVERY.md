# ОКОЁМ · Доставка артефактов учредителю

Версия 1.0 · 13.08.2026 · владелец документа: COO
Документ обслуживает ритмы устава `00_ORG_CHARTER §3`: ночной daily в 05:00 и борд C-1 в понедельник и четверг в 10:00.

---

## 0. Главное ограничение, из которого следует всё остальное

Каждый запуск по расписанию — новая чистая сессия в новом контейнере. Файлы на диске не переживают сессию: `/root/ops/tools/` в начале каждого запуска пуст.

Отсюда правило: **инструменты не хранятся, а воссоздаются**. Единственный носитель — этот документ в проекте. Порядок каждой сессии:

1. прочитать этот документ;
2. записать оба скрипта из раздела 8 на диск;
3. запустить.

Поэтому раздел 8 содержит полные исходники, а не ссылки на них. Любая правка скрипта, не перенесённая в раздел 8, исчезнет вместе с контейнером.

---

## 1. Что доставляется и когда

| Ритм | Время (МСК) | Что уходит | Формат |
|---|---|---|---|
| Ночной daily | ежедневно, 05:00 | сводка цикла | одно сообщение, не более 15 строк |
| Борд C-1 | понедельник и четверг, 10:00 | короткий текст плюс дек | сообщение плюс HTML-файл |
| Бюджетный борд | первый понедельник месяца | то же плюс маркетинг-план | сообщение плюс два файла |
| Алерт по порогу | по факту | одна строка со знаком ⚠️ | сообщение вне расписания |

Правило объёма: если сводка не помещается в 15 строк, режется содержание, а не увеличивается сообщение. Всё длинное живёт в `20_DAILY_LOG` и в деке.

---

## 2. Канал и секреты

Канал один: Telegram-бот **@cmpas_board_bot**, личный чат с учредителем.

Секреты приходят **из окружения контейнера** и нигде больше не лежат:

- `TG_BOT_TOKEN` — токен бота от @BotFather;
- `TG_CHAT_ID` — идентификатор чата учредителя;
- `TG_API_ROOT` — необязательный, адрес зеркала Bot API, если `api.telegram.org` недоступен из контейнера.

Проверка в начале сессии — по факту наличия, без печати значений:

```bash
[ -n "$TG_BOT_TOKEN" ] && [ -n "$TG_CHAT_ID" ] && echo "доступ есть" || echo "доступа нет"
```

Чего не делаем никогда: не печатаем токен в лог, не кладём его в проект, в дек, в этот документ и в имена файлов. Если переменных нет — это не повод чинить руками, а задача учредителю по `00_ORG_CHARTER §6.2` (доступы и токены). Формулировка задачи — в разделе 7.

---

## 3. Инструменты

### 3.1. `tools/tg_send.py` — отправка

Только стандартная библиотека (urllib): pip в контейнере может быть недоступен. Коды выхода: 0 — доставлено, 1 — нет.

```bash
# сводка ночного цикла: готовая разметка, поэтому --html
python3 /root/ops/tools/tg_send.py text --from-file /tmp/summary.html --html

# то же из stdin
cat /tmp/summary.html | python3 /root/ops/tools/tg_send.py text - --html

# произвольный текст из внешнего источника: без --html, теги экранируются
python3 /root/ops/tools/tg_send.py text "Отзыв пользователя: <не читается>"

# дек борда файлом
python3 /root/ops/tools/tg_send.py file /root/ops/docs/board/board_2026-08-13_N14.html \
  --caption "Борд C-1 № 14 · 13 августа 2026 · 6 слайдов"

# картинка
python3 /root/ops/tools/tg_send.py photo /tmp/slide1.png --caption "Слайд 1"

# проверка сборки сообщения без отправки
python3 /root/ops/tools/tg_send.py --dry-run text --from-file /tmp/summary.html --html
```

Что скрипт делает сам: режет текст длиннее 4096 символов по границам строк, обрезает подпись до 1024 символов, повторяет попытку при сетевом сбое, 5xx и 429 (уважая `retry_after`), не повторяет при 400/401/403 — там повтор бессмыслен.

Что нужно помнить: `--html` отключает экранирование. Разметку в шаблонах держим построчно — открывающий и закрывающий тег в одной строке, иначе нарезка длинного сообщения может разорвать тег.

### 3.2. `tools/make_deck.py` — сборка дека

```bash
python3 /root/ops/tools/make_deck.py /root/ops/docs/board/board_2026-08-13_N14.md \
     -o /root/ops/docs/board/board_2026-08-13_N14.html
```

На выходе — один самодостаточный HTML: весь CSS и JS внутри, ни одного внешнего запроса, ни одного обращения к хранилищу браузера. Открывается с телефона и с ноутбука, печатается по одному слайду на страницу.

Формат входа:

| Разметка | Что делает |
|---|---|
| `@title`, `@date`, `@meeting` | заголовок дека, дата, номер заседания — обязательны |
| `---` | разделитель слайдов |
| `# Заголовок` | заголовок слайда |
| `## Подзаголовок` | подзаголовок |
| `- пункт` | буллет |
| `\| a \| b \|` | таблица markdown |
| `@metric Имя \| значение \| дельта \| комментарий` | плитка метрики, дельта подкрашивается |
| `@status зелёный\|жёлтый\|красный текст` | строка статуса с цветной точкой |
| `@human задача` | блок задач учредителю, единственный выделенный блок |
| `> примечание` | сноска мелким шрифтом |

Дельта считается по первому знаку: `+` — рост, зелёный; `-` или `−` — падение, терракотовый; всё прочее — нейтрально, серый. Подкраска приглушённая: дек читают спокойно, а не тушат пожар.

Правило по эмодзи: в деке их нет. Исключения ровно два — предупреждающий знак ⚠ (появляется сам у красного статуса) и цветные точки статусов.

Проверка перед отправкой (если в контейнере есть Playwright):

```bash
python3 - <<'EOF'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width":1440,"height":900})
    pg.goto("file:///root/ops/docs/board/board_2026-08-13_N14.html#s1")
    pg.screenshot(path="/tmp/s1.png"); b.close()
EOF
```

Смотреть на снимок обязательно: переполнение, обрезанный текст и разъехавшаяся таблица видны только глазами.

---

## 4. Шаблон сводки ночного цикла

Одно сообщение, `parse_mode=HTML`, не более 15 строк. Подставляются значения; строка, для которой нет данных, **удаляется целиком** — выдумывать динамику запрещено уставом (`§4`, правило честности данных). Если данных нет вообще, вместо метрик идёт одна строка «данных за сутки нет, работаем по гипотезам».

```
<b>Ночной цикл · {дата}, 05:00</b>
ПРАКТИКА · {метрика} {значение} · {дельта}
ЗАПИСКИ · {метрика} {значение} · {дельта}
МОМЕНТЫ · {метрика} {значение} · {дельта}
⚠️ {метрика} пробила порог — заведено P0
<b>План дня</b>
P0 · {задача} — {владелец}
P1 · {задача} — {владелец}
P1 · {задача} — {владелец}
<b>Отрезано</b> {что} — {почему}
<b>Учредителю</b> {n} задач · блокирующая: {одна строкой}
✓ {что закрыто за сутки}
<i>Подробно — 20_DAILY_LOG · бэклог — 10_BACKLOG</i>
```

Заполненный пример:

```
<b>Ночной цикл · 14 августа, 05:00</b>
ПРАКТИКА · активных 214 · +11%
ЗАПИСКИ · удержание 41% · −5 п.п.
МОМЕНТЫ · регистраций 1 340 · +26%
⚠️ Удержание ЗАПИСОК пробило порог 45% — заведено P0
<b>План дня</b>
P0 · Откат лишних шагов сохранения заметки — PO-ЗАПИСКИ
P1 · Перенос биллинга на единый аккаунт — CTO
P1 · Экран второй сессии без пустого старта — PO-МОМЕНТЫ
<b>Отрезано</b> онбординг специалиста v2 — занята ёмкость разработки
<b>Учредителю</b> 3 задачи · блокирующая: токен доступа к аналитике
✓ Закрыт P0 по оплате из мобильного веба
<i>Подробно — 20_DAILY_LOG · бэклог — 10_BACKLOG</i>
```

Эмодзи в сводке допустимы ровно два: ⚠️ у пробитого порога и ✓ у закрытого. Больше — нет.

---

## 5. Шаблон сообщения борда

Сначала короткий текст, следом дек файлом. Текст — не пересказ дека, а причина его открыть.

```
<b>Борд C-1 № {N} · {дата}</b>
Решений принято {n} · просрочено с прошлого борда {m}
⚠️ {главный риск одной строкой}
<b>Учредителю</b> {k} задач · блокирующая: {одна строкой}
Дек ниже файлом, {s} слайдов. Открывается с телефона.
```

Подпись к файлу:

```
Борд C-1 № {N} · {дата} · {s} слайдов
```

Порядок вызовов:

```bash
python3 /root/ops/tools/tg_send.py text --from-file /tmp/board_msg.html --html
python3 /root/ops/tools/tg_send.py file /root/ops/docs/board/board_2026-08-13_N14.html \
  --caption "Борд C-1 № 14 · 13 августа 2026 · 6 слайдов"
```

Сначала текст, потом файл: в ленте учредителя сперва появляется смысл, следом вложение.

---

## 6. Именование файлов деков

```
board_ГГГГ-ММ-ДД_N<номер заседания>.html
board_ГГГГ-ММ-ДД_N<номер заседания>.md      — исходник, кладётся рядом
```

Примеры: `board_2026-08-13_N14.html`, `board_2026-09-01_N19_budget.html`.

Правила:

- дата — дата заседания, не дата сборки;
- номер заседания сквозной, из `21_BOARD_LOG`, не сбрасывается ни в новом месяце, ни в новом квартале;
- суффикс через подчёркивание — только для особых заседаний: `_budget` (бюджетный борд), `_retro` (ретро квартала), `_urgent` (внеочередной);
- только латиница, цифры, дефис и подчёркивание: имя проходит через Telegram, файловую систему и почту без сюрпризов;
- каталог: `/root/ops/docs/board/` внутри сессии, `claude/ops/board/` в проекте;
- пересборка того же заседания перезаписывает файл, версии в имени не плодим.

---

## 7. Если Telegram недоступен

Недоступность — это ненулевой код выхода `tg_send.py` после всех повторов: блокировка, отсутствующий токен, сбой сети.

**Шаг 1. Повторить один раз.** Внутри скрипта уже пять попыток с растущей паузой. Если он вышел с 1 — подождать 5 минут и запустить ещё раз. Дважды не удалось — считаем канал недоступным и не долбимся дальше.

**Шаг 2. Положить артефакт в проект.** Проект — вторая точка правды, он переживает контейнер:

- дек: `project_write` в `claude/ops/board/board_2026-08-13_N14.html`;
- текст сводки или сообщения борда: `project_write` в `claude/ops/board/undelivered_2026-08-14_daily.md`.

**Шаг 3. Пометить в очереди доставки.** Строка в `claude/ops/22_DELIVERY_QUEUE.md`:

```
| 2026-08-14 05:07 | ночная сводка | claude/ops/board/undelivered_2026-08-14_daily.md | не доставлено | причина: 403 от api.telegram.org |
```

**Шаг 4. Завести задачу учредителю**, если причина в доступах. По `§6.4` устава — с глаголом, ценой и сроком:

```
Проверить бота @cmpas_board_bot и выдать действующий TG_BOT_TOKEN в окружение расписания.
Без него сводки и деки не уходят третий цикл подряд, артефакты лежат в claude/ops/board/. 10 минут.
```

**Шаг 5. Первое действие следующей сессии** — прочитать `22_DELIVERY_QUEUE.md`. Если в нём есть недоставленное и канал ожил: отправить старое **до** нового, в хронологическом порядке, с пометкой в первой строке `<i>Доставлено с задержкой: цикл 14.08</i>`, и закрыть строки очереди. Очередь не растёт бесконечно: старше 7 дней — сжимается в одну строку `12_DECISIONS` и удаляется.

Чего не делаем: не дублируем доставку в другие каналы по своей инициативе, не пишем учредителю с личных адресов, не публикуем дек по внешней ссылке. Канал меняет только учредитель.

---

## 8. Восстановление инструментов

Полные исходники. Порядок: создать каталог, записать оба файла, проверить синтаксис.

```bash
mkdir -p /root/ops/tools /root/ops/docs/board
# далее записать содержимое двух блоков ниже в файлы:
#   /root/ops/tools/tg_send.py
#   /root/ops/tools/make_deck.py
python3 -m py_compile /root/ops/tools/tg_send.py /root/ops/tools/make_deck.py && echo "инструменты на месте"
```

Проверка после восстановления, без отправки и без сети:

```bash
python3 /root/ops/tools/tg_send.py --dry-run text "проверка связи"
python3 /root/ops/tools/make_deck.py /root/ops/tools/demo_deck.md -o /tmp/check.html && echo "дек собирается"
```

### 8.1. `/root/ops/tools/tg_send.py`

```python
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
```

### 8.2. `/root/ops/tools/make_deck.py`

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ОКОЁМ · генератор HTML-дека для борда C-1.

Вход  — markdown-подобный текстовый файл (см. формат ниже).
Выход — один самодостаточный HTML-файл: весь CSS и JS внутри, ни одного
внешнего запроса, ни одного обращения к localStorage. Файл можно отправить
в Telegram документом, открыть с телефона и распечатать.

Только стандартная библиотека Python.

ФОРМАТ ВХОДА
------------
Мета-строки (в любом месте, обычно в шапке файла до первого разделителя):

    @title Борд C-1 · ОКОЁМ
    @date 13 августа 2026
    @meeting 14

Слайды разделяются строкой из трёх дефисов на отдельной строке: ---
Первая содержательная строка слайда — заголовок:

    # Контроль исполнения решений
    ## Решения борда № 13                          подзаголовок
    - обычный буллет                               буллеты
    | Продукт | Метрика | Значение |               таблица markdown
    |---|---|---|
    @metric Активные специалисты | 214 | +12% | рост за счёт ЗАПИСОК
    @status жёлтый Два решения просрочены без объяснения
    @human Выдать токен доступа к AppMetrica — 10 минут
    > сноска мелким шрифтом

Инлайн-разметка: **жирный**, _курсив_, `моноширинный`.

ЗАПУСК
------
    python3 make_deck.py input.md -o board.html
"""

import argparse
import html
import os
import re
import sys

# --------------------------------------------------------------------------
# Разбор входного файла
# --------------------------------------------------------------------------

STATUS_COLORS = {
    "зелёный": "green", "зеленый": "green", "green": "green", "ок": "green",
    "жёлтый": "amber", "желтый": "amber", "amber": "amber", "yellow": "amber",
    "красный": "red", "red": "red",
}


def parse_deck(text):
    """Разбирает текст в (мета, список слайдов). Слайд — заголовок плюс блоки."""
    meta = {"title": "Борд C-1", "date": "", "meeting": ""}
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # Мета-строки вынимаем заранее, чтобы они не мешали разбору слайдов.
    body = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("@title "):
            meta["title"] = stripped[7:].strip()
        elif stripped.startswith("@date "):
            meta["date"] = stripped[6:].strip()
        elif stripped.startswith("@meeting "):
            meta["meeting"] = stripped[9:].strip()
        else:
            body.append(line)

    # Разделитель слайдов — строка ровно из дефисов (три и больше).
    raw_slides, current = [], []
    for line in body:
        if re.fullmatch(r"-{3,}\s*", line.strip()):
            raw_slides.append(current)
            current = []
        else:
            current.append(line)
    raw_slides.append(current)

    slides = [parse_slide(chunk) for chunk in raw_slides]
    slides = [s for s in slides if s["title"] or s["blocks"]]
    return meta, slides


def parse_slide(lines):
    """Разбирает один слайд в структуру {title, blocks}."""
    slide = {"title": "", "blocks": []}
    blocks = slide["blocks"]
    buffer_kind, buffer_items = None, []

    def flush():
        """Закрывает накопленный однородный блок (буллеты, таблица, метрики...)."""
        nonlocal buffer_kind, buffer_items
        if buffer_kind and buffer_items:
            blocks.append({"kind": buffer_kind, "items": buffer_items})
        buffer_kind, buffer_items = None, []

    def push(kind, item):
        nonlocal buffer_kind, buffer_items
        if buffer_kind != kind:
            flush()
            buffer_kind = kind
        buffer_items.append(item)

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            flush()
            continue

        if stripped.startswith("# "):
            flush()
            if slide["title"]:
                # Второй заголовок на слайде трактуем как подзаголовок,
                # чтобы кривой вход не терял текст молча.
                blocks.append({"kind": "sub", "items": [stripped[2:].strip()]})
            else:
                slide["title"] = stripped[2:].strip()
        elif stripped.startswith("## "):
            flush()
            blocks.append({"kind": "sub", "items": [stripped[3:].strip()]})
        elif stripped.startswith("- ") or stripped == "-":
            push("bullets", stripped[2:].strip())
        elif stripped.startswith("|"):
            push("table", stripped)
        elif stripped.startswith("@metric "):
            push("metrics", parse_metric(stripped[8:]))
        elif stripped.startswith("@status "):
            flush()
            blocks.append({"kind": "status", "items": [parse_status(stripped[8:])]})
        elif stripped.startswith("@human"):
            push("human", stripped[6:].strip().lstrip(":").strip())
        elif stripped.startswith("> "):
            push("note", stripped[2:].strip())
        else:
            push("para", stripped)

    flush()
    return slide


def parse_metric(payload):
    """`Название | значение | дельта | комментарий` → словарь с направлением дельты."""
    parts = [p.strip() for p in payload.split("|")]
    while len(parts) < 4:
        parts.append("")
    name, value, delta, note = parts[0], parts[1], parts[2], " | ".join(parts[3:]).strip()
    return {"name": name, "value": value, "delta": delta,
            "dir": delta_direction(delta), "note": note}


def delta_direction(delta):
    """Направление дельты: рост / падение / нейтрально. Определяем по первому знаку."""
    d = delta.strip()
    if not d or d in {"0", "—", "-", "–", "0%", "без изменений"}:
        return "flat"
    if d[0] in "+↑▲":
        return "up"
    if d[0] in "-−–↓▼":
        return "down"
    return "flat"


def parse_status(payload):
    """`зелёный|жёлтый|красный текст` → (класс цвета, текст)."""
    parts = payload.strip().split(None, 1)
    word = parts[0].lower().strip(":|,") if parts else ""
    color = STATUS_COLORS.get(word)
    if color is None:
        # Цвет не распознан — считаем весь текст статусом нейтрального цвета.
        return {"color": "amber", "text": payload.strip()}
    return {"color": color, "text": parts[1].strip() if len(parts) > 1 else ""}


# --------------------------------------------------------------------------
# Инлайн-разметка
# --------------------------------------------------------------------------

def inline(text):
    """Экранирует HTML и включает инлайн-разметку: **жирный**, _курсив_, `код`."""
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<em>\1</em>", out)
    out = re.sub(r"`(.+?)`", r"<code>\1</code>", out)
    return out


# --------------------------------------------------------------------------
# Рендер блоков
# --------------------------------------------------------------------------

def render_table(rows):
    """Таблица markdown → HTML. Строка-разделитель определяет наличие шапки."""
    grid = []
    for row in rows:
        cells = row.strip().strip("|").split("|")
        grid.append([c.strip() for c in cells])
    if not grid:
        return ""

    has_header = False
    if len(grid) > 1 and all(re.fullmatch(r":?-{2,}:?", c) or c == "" for c in grid[1]):
        has_header = True
        header, body = grid[0], grid[2:]
    else:
        header, body = None, grid

    width = max(len(r) for r in grid)
    parts = ['<div class="table-wrap"><table>']
    if has_header and header:
        cells = "".join("<th>%s</th>" % inline(c) for c in header)
        parts.append("<thead><tr>%s</tr></thead>" % cells)
    parts.append("<tbody>")
    labels = (header or []) + [""] * width
    for row in body:
        row = row + [""] * (width - len(row))
        # data-label нужен для телефона: там таблица разворачивается в стопку
        # «подпись — значение», иначе колонки уезжают за край экрана.
        cells = "".join(
            '<td data-label="%s">%s</td>'
            % (html.escape(labels[i], quote=True), inline(c))
            for i, c in enumerate(row)
        )
        parts.append("<tr>%s</tr>" % cells)
    parts.append("</tbody></table></div>")
    return "".join(parts)


def render_metrics(items):
    """Плитка метрик. Дельта подкрашивается приглушённо: рост — зелень, падение — терракот."""
    cards = []
    for m in items:
        delta_html = ""
        if m["delta"]:
            delta_html = '<div class="m-delta m-%s">%s</div>' % (m["dir"], inline(m["delta"]))
        note_html = '<div class="m-note">%s</div>' % inline(m["note"]) if m["note"] else ""
        cards.append(
            '<div class="metric">'
            '<div class="m-name">%s</div>'
            '<div class="m-row"><div class="m-value">%s</div>%s</div>'
            '%s</div>' % (inline(m["name"]), inline(m["value"]), delta_html, note_html)
        )
    return '<div class="metrics">%s</div>' % "".join(cards)


def render_status(item):
    """Строка статуса: цветная точка плюс текст. Красный получает предупреждающий знак."""
    mark = '<span class="warn">&#9888;</span>' if item["color"] == "red" else ""
    return ('<div class="status status-%s"><span class="dot"></span>%s'
            '<span class="status-text">%s</span></div>'
            % (item["color"], mark, inline(item["text"])))


def render_human(items):
    """Блок задач учредителю — единственный визуально выделенный блок в деке."""
    rows = "".join("<li>%s</li>" % inline(i) for i in items if i)
    return ('<div class="human"><div class="human-title">Учредителю</div>'
            '<ul class="human-list">%s</ul></div>' % rows)


def render_blocks(blocks):
    out = []
    for block in blocks:
        kind, items = block["kind"], block["items"]
        if kind == "sub":
            out.append('<p class="sub">%s</p>' % inline(items[0]))
        elif kind == "bullets":
            out.append("<ul class=\"bullets\">%s</ul>"
                       % "".join("<li>%s</li>" % inline(i) for i in items))
        elif kind == "table":
            out.append(render_table(items))
        elif kind == "metrics":
            out.append(render_metrics(items))
        elif kind == "status":
            out.append(render_status(items[0]))
        elif kind == "human":
            out.append(render_human(items))
        elif kind == "note":
            out.append('<p class="note">%s</p>' % "<br>".join(inline(i) for i in items))
        elif kind == "para":
            for i in items:
                out.append("<p>%s</p>" % inline(i))
    return "\n".join(out)


# --------------------------------------------------------------------------
# Оформление: спокойная светлая типографика в палитре бренда
# --------------------------------------------------------------------------

CSS = """
:root{
  --bg:#faf8f5;          /* фон бренда */
  --surface:#ffffff;
  --surface-2:#f4f0e9;
  --ink:#23211d;
  --ink-soft:#4d4841;
  --muted:#7b746a;
  --line:#e4ddd1;
  --green:#1a4d3a;       /* тёмно-зелёный знака */
  --gold:#c9a961;        /* акцент бренда */
  --up:#2f6b4f;
  --down:#a4483c;
  --amber:#b8862f;
  --human-bg:#f6f1e4;
  --shadow:0 1px 2px rgba(35,33,29,.05), 0 8px 26px rgba(35,33,29,.05);
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#171613;
    --surface:#1f1e1a;
    --surface-2:#26241f;
    --ink:#ece7dd;
    --ink-soft:#cdc6ba;
    --muted:#9b9488;
    --line:#332f28;
    --green:#8fbfa6;
    --gold:#d6bd85;
    --up:#8bc3a5;
    --down:#d59386;
    --amber:#d9ae5e;
    --human-bg:#26221a;
    --shadow:none;
  }
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--bg);
  color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",
              "Noto Sans",Arial,sans-serif;
  font-size:18px;
  line-height:1.55;
  -webkit-text-size-adjust:100%;
  font-feature-settings:"liga" 1,"kern" 1;
}
.deck{width:100%}

/* --- каркас слайда --- */
.slide{
  display:none;
  min-height:100vh;
  padding:clamp(20px,3.4vw,54px) clamp(18px,4.6vw,76px) clamp(74px,7vw,88px);
  flex-direction:column;
  max-width:1240px;
  margin:0 auto;
}
.slide.active{display:flex}
.slide-top{
  display:flex;justify-content:space-between;align-items:baseline;gap:16px;
  font-size:.72em;letter-spacing:.09em;text-transform:uppercase;
  color:var(--muted);border-bottom:1px solid var(--line);
  padding-bottom:12px;margin-bottom:clamp(18px,2.6vw,34px);flex-wrap:wrap;
}
.brand{color:var(--green);font-weight:650;letter-spacing:.16em}
h1{
  font-size:clamp(27px,3.6vw,44px);
  line-height:1.16;font-weight:640;letter-spacing:-.012em;
  margin:0 0 clamp(12px,1.6vw,20px);color:var(--ink);
  max-width:22ch;
}
.slide-body{flex:1 1 auto;min-width:0}
.slide-body > * + *{margin-top:clamp(14px,1.7vw,22px)}
p{margin:0;color:var(--ink-soft);max-width:68ch;font-size:clamp(16px,1.25vw,20px)}
.sub{
  color:var(--muted);font-size:clamp(17px,1.45vw,22px);
  font-weight:500;max-width:56ch;margin-top:0;
}
h1 + .slide-body > .sub:first-child{margin-top:-6px}
strong{font-weight:640;color:var(--ink)}
em{font-style:italic}
code{
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:.88em;background:var(--surface-2);
  padding:.08em .34em;border-radius:4px;
}

/* --- буллеты --- */
ul.bullets{margin:0;padding:0;list-style:none;max-width:66ch}
ul.bullets li{
  position:relative;padding-left:1.35em;margin:0 0 .58em;
  font-size:clamp(16px,1.25vw,20px);color:var(--ink-soft);
}
ul.bullets li:last-child{margin-bottom:0}
ul.bullets li::before{
  content:"";position:absolute;left:.15em;top:.66em;
  width:6px;height:6px;border-radius:50%;background:var(--gold);
}

/* --- метрики --- */
.metrics{
  display:grid;gap:clamp(10px,1.1vw,16px);
  grid-template-columns:repeat(auto-fit,minmax(232px,1fr));
}
.metric{
  background:var(--surface);border:1px solid var(--line);
  border-radius:12px;padding:clamp(14px,1.3vw,20px);box-shadow:var(--shadow);
  min-width:0;display:flex;flex-direction:column;
}
.m-name{
  font-size:.78em;letter-spacing:.05em;text-transform:uppercase;
  color:var(--muted);margin-bottom:.45em;line-height:1.35;
  /* Две строки под название: значения выравниваются по одной линии во всём ряду. */
  min-height:2.7em;
}
.m-row{display:flex;align-items:baseline;gap:.5em;flex-wrap:wrap}
.m-value{
  font-size:clamp(28px,2.9vw,38px);font-weight:640;
  line-height:1.05;letter-spacing:-.02em;color:var(--ink);
  font-variant-numeric:tabular-nums;
}
.m-delta{font-size:clamp(14px,1.05vw,17px);font-weight:600;white-space:nowrap}
.m-up{color:var(--up)}
.m-down{color:var(--down)}
.m-flat{color:var(--muted)}
.m-note{margin-top:auto;padding-top:.5em;font-size:.82em;color:var(--muted);line-height:1.4}

/* --- таблицы --- */
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{
  border-collapse:collapse;width:100%;
  font-size:clamp(15px,1.15vw,18px);
  background:var(--surface);border:1px solid var(--line);border-radius:12px;
  overflow:hidden;
}
thead th{
  text-align:left;font-weight:600;font-size:.84em;
  letter-spacing:.05em;text-transform:uppercase;color:var(--muted);
  background:var(--surface-2);
  padding:.72em .9em;border-bottom:1px solid var(--line);white-space:nowrap;
}
td{
  padding:.68em .9em;border-bottom:1px solid var(--line);
  color:var(--ink-soft);vertical-align:top;
}
tbody tr:last-child td{border-bottom:none}
td:first-child{color:var(--ink);font-weight:520}

/* --- статус --- */
.status{
  display:flex;align-items:baseline;gap:.6em;
  background:var(--surface);border:1px solid var(--line);
  border-left:3px solid var(--line);
  border-radius:10px;padding:.72em 1em;
  font-size:clamp(16px,1.2vw,19px);color:var(--ink-soft);
}
.status .dot{
  width:10px;height:10px;border-radius:50%;flex:0 0 auto;
  transform:translateY(1px);
}
.status-green{border-left-color:#2f6b4f}
.status-green .dot{background:#2f6b4f}
.status-amber{border-left-color:var(--amber)}
.status-amber .dot{background:var(--amber)}
.status-red{border-left-color:var(--down)}
.status-red .dot{background:var(--down)}
.warn{color:var(--down);font-size:1.05em;line-height:1}

/* --- задачи учредителю --- */
.human{
  background:var(--human-bg);border:1px solid var(--gold);
  border-radius:12px;padding:clamp(14px,1.4vw,22px) clamp(16px,1.6vw,26px);
}
.human-title{
  font-size:.76em;letter-spacing:.14em;text-transform:uppercase;
  color:var(--gold);font-weight:650;margin-bottom:.6em;
}
.human-list{margin:0;padding:0;list-style:none;counter-reset:h}
.human-list li{
  counter-increment:h;position:relative;padding-left:1.9em;
  margin-bottom:.6em;font-size:clamp(16px,1.25vw,20px);color:var(--ink);
  max-width:78ch;
}
.human-list li:last-child{margin-bottom:0}
.human-list li::before{
  content:counter(h);position:absolute;left:0;top:.05em;
  width:1.35em;height:1.35em;border-radius:50%;
  background:var(--gold);color:#2a2213;
  font-size:.78em;font-weight:700;
  display:flex;align-items:center;justify-content:center;
}

/* --- сноска --- */
.note{
  font-size:clamp(14px,1vw,16px);color:var(--muted);
  border-left:2px solid var(--line);padding-left:.9em;max-width:78ch;
}

/* --- подвал и навигация --- */
@media screen{.slide-foot .foot-num{display:none}}
.slide-foot{
  margin-top:clamp(18px,2.4vw,32px);padding-top:12px;
  border-top:1px solid var(--line);
  display:flex;justify-content:space-between;align-items:center;
  font-size:.76em;color:var(--muted);letter-spacing:.05em;
}
.nav{
  position:fixed;right:clamp(12px,2vw,28px);bottom:clamp(12px,2vw,26px);
  display:flex;align-items:center;gap:8px;z-index:10;
  background:var(--surface);border:1px solid var(--line);
  border-radius:999px;padding:5px 8px;box-shadow:var(--shadow);
}
.nav button{
  font:inherit;font-size:19px;line-height:1;
  width:38px;height:38px;border-radius:50%;
  border:none;background:transparent;color:var(--ink);
  cursor:pointer;display:flex;align-items:center;justify-content:center;
}
.nav button:hover{background:var(--surface-2)}
.nav button:disabled{opacity:.3;cursor:default;background:transparent}
.nav .counter{
  font-size:14px;color:var(--muted);min-width:52px;text-align:center;
  font-variant-numeric:tabular-nums;letter-spacing:.04em;
}
.progress{
  position:fixed;left:0;top:0;height:2px;background:var(--gold);
  width:0;transition:width .18s ease;z-index:11;
}

@media (max-width:640px){
  body{font-size:17px}
  .slide{padding:16px 18px 84px}
  .slide-top{font-size:.68em}
  h1{max-width:none}
  .metrics{grid-template-columns:1fr}
  .m-name{min-height:0}
  .m-note{margin-top:.5em}
  /* Таблица на телефоне разворачивается в стопку «подпись — значение». */
  table,tbody,tr,td{display:block;width:100%}
  thead{display:none}
  .table-wrap{overflow-x:visible}
  tbody tr{padding:.8em .95em;border-bottom:1px solid var(--line)}
  tbody tr:last-child{border-bottom:none}
  td{border:none;padding:.16em 0;display:flex;gap:.7em;align-items:baseline}
  td:not([data-label=""])::before{
    content:attr(data-label);flex:0 0 40%;max-width:40%;
    color:var(--muted);font-size:.78em;letter-spacing:.05em;
    text-transform:uppercase;line-height:1.5;
  }
  /* Первая ячейка — заголовок карточки строки, подпись ей не нужна. */
  td:first-child{display:block;font-weight:650;margin-bottom:.4em;line-height:1.35}
  td:first-child::before{content:none}
  .nav{padding:4px 6px}
  .nav button{width:34px;height:34px}
}

/* --- печать: один слайд на страницу --- */
@media print{
  @page{size:A4 landscape;margin:12mm}
  body{background:#fff;color:#000;font-size:12pt}
  .nav,.progress{display:none !important}
  .slide{
    display:flex !important;min-height:auto;height:auto;
    page-break-after:always;break-after:page;
    padding:0 0 8mm;max-width:none;
  }
  .slide:last-child{page-break-after:auto;break-after:auto}
  .metric,table,.status,.human{break-inside:avoid;box-shadow:none}
  h1{font-size:22pt}
  .m-value{font-size:20pt}
}
"""

JS = """
(function(){
  var slides = Array.prototype.slice.call(document.querySelectorAll('.slide'));
  var total = slides.length;
  var counter = document.getElementById('counter');
  var prev = document.getElementById('prev');
  var next = document.getElementById('next');
  var progress = document.getElementById('progress');
  var index = 0;

  function fromHash(){
    var n = parseInt((location.hash || '').replace('#s',''), 10);
    return (isFinite(n) && n >= 1 && n <= total) ? n - 1 : 0;
  }

  function show(i, updateHash){
    index = Math.max(0, Math.min(total - 1, i));
    slides.forEach(function(s, k){ s.classList.toggle('active', k === index); });
    counter.textContent = (index + 1) + ' / ' + total;
    prev.disabled = index === 0;
    next.disabled = index === total - 1;
    progress.style.width = ((index + 1) / total * 100) + '%';
    if (updateHash !== false){
      // Состояние держим только в адресной строке, хранилище браузера не трогаем.
      history.replaceState(null, '', '#s' + (index + 1));
    }
    window.scrollTo(0, 0);
  }

  prev.addEventListener('click', function(){ show(index - 1); });
  next.addEventListener('click', function(){ show(index + 1); });

  document.addEventListener('keydown', function(e){
    if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') { show(index + 1); e.preventDefault(); }
    else if (e.key === 'ArrowLeft' || e.key === 'PageUp') { show(index - 1); e.preventDefault(); }
    else if (e.key === 'Home') { show(0); e.preventDefault(); }
    else if (e.key === 'End') { show(total - 1); e.preventDefault(); }
  });

  // Листание пальцем на телефоне.
  var x0 = null, y0 = null;
  document.addEventListener('touchstart', function(e){
    x0 = e.changedTouches[0].clientX; y0 = e.changedTouches[0].clientY;
  }, {passive:true});
  document.addEventListener('touchend', function(e){
    if (x0 === null) return;
    var dx = e.changedTouches[0].clientX - x0;
    var dy = e.changedTouches[0].clientY - y0;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.8) show(index + (dx < 0 ? 1 : -1));
    x0 = null; y0 = null;
  }, {passive:true});

  window.addEventListener('hashchange', function(){ show(fromHash(), false); });
  show(fromHash(), false);
})();
"""


# --------------------------------------------------------------------------
# Сборка HTML
# --------------------------------------------------------------------------

def build_html(meta, slides):
    title = meta["title"] or "Борд C-1"
    date = meta["date"]
    meeting = meta["meeting"]

    meeting_label = ("Заседание № %s" % meeting) if meeting else ""
    top_right = " · ".join(x for x in (meeting_label, date) if x)

    sections = []
    for number, slide in enumerate(slides, start=1):
        head = ('<header class="slide-top">'
                '<span class="brand">ОКОЁМ</span>'
                '<span>%s</span></header>' % html.escape(top_right, quote=False))
        heading = "<h1>%s</h1>" % inline(slide["title"]) if slide["title"] else ""
        body = render_blocks(slide["blocks"])
        foot = ('<footer class="slide-foot"><span>%s</span>'
                '<span class="foot-num">%d / %d</span></footer>'
                % (html.escape(title, quote=False), number, len(slides)))
        sections.append(
            '<section class="slide" id="s%d">%s%s<div class="slide-body">%s</div>%s</section>'
            % (number, head, heading, body, foot)
        )

    page_title = " · ".join(x for x in (title, meeting_label, date) if x)

    return (
        "<!DOCTYPE html>\n"
        '<html lang="ru">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        "<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n"
        '<div class="progress" id="progress"></div>\n'
        '<div class="deck">\n%s\n</div>\n'
        '<nav class="nav" aria-label="Навигация по слайдам">'
        '<button id="prev" type="button" aria-label="Предыдущий слайд">&#8249;</button>'
        '<span class="counter" id="counter"></span>'
        '<button id="next" type="button" aria-label="Следующий слайд">&#8250;</button>'
        "</nav>\n<script>%s</script>\n</body>\n</html>\n"
        % (html.escape(page_title, quote=False), CSS, "\n".join(sections), JS)
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="make_deck.py",
        description="ОКОЁМ · сборка самодостаточного HTML-дека борда из markdown-подобного файла.",
    )
    parser.add_argument("input", help="входной файл (.md)")
    parser.add_argument("-o", "--output", default=None,
                        help="выходной HTML (по умолчанию — рядом со входным)")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.input):
        sys.stderr.write("ОШИБКА: входной файл не найден: %s\n" % args.input)
        return 1

    with open(args.input, "r", encoding="utf-8") as handle:
        text = handle.read()

    meta, slides = parse_deck(text)
    if not slides:
        sys.stderr.write("ОШИБКА: во входном файле нет ни одного слайда "
                         "(нужен хотя бы один блок с заголовком '# ').\n")
        return 1

    output = args.output or os.path.splitext(args.input)[0] + ".html"
    with open(output, "w", encoding="utf-8") as handle:
        handle.write(build_html(meta, slides))

    size = os.path.getsize(output)
    sys.stderr.write("готово: %s — слайдов %d, размер %.1f КБ\n" % (output, len(slides), size / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### 8.3. `/root/ops/tools/demo_deck.md` — образец входа

Шесть слайдов по повестке борда из `00_ORG_CHARTER §5`. Годится как каркас настоящего дека: заменяются цифры и формулировки, структура остаётся.

```markdown
@title Борд C-1 · ОКОЁМ
@date 13 августа 2026, четверг
@meeting 14

---

# Контроль исполнения решений

## Решения борда № 13 от 10 августа

| Решение | Владелец | Срок | Статус |
|---|---|---|---|
| Ввести порог алерта по удержанию ЗАПИСОК | CPO | 12.08 | сделано |
| Оферта под 152-ФЗ, редакция 3 | Legal | 12.08 | сделано |
| Перенести биллинг на единый аккаунт | CTO | 13.08 | в работе |
| План контента МОМЕНТОВ на сентябрь | CMO | 12.08 | просрочено |
| Сверка расходов на подрядчиков за июль | Бухгалтер | 11.08 | отменено |

@status жёлтый Одно решение просрочено: контент-план МОМЕНТОВ. Причина — CMO ждал данных по каналам, которые не пришли. Новый срок 15.08, данные заводятся вручную.

> Отменённое решение снято CFO: сверка бессмысленна до закрытия июльских актов.

---

# Метрики недели

## Три продукта, срез на утро 13 августа

@metric ПРАКТИКА · активные специалисты | 214 | +11% | 21 новый, отток 3
@metric ПРАКТИКА · платящие | 68 | +4% | конверсия из триала 24%
@metric ЗАПИСКИ · записей на специалиста | 6,2 | −8% | падение вторую неделю
@metric ЗАПИСКИ · недельное удержание | 41% | −5 п.п. | пробит порог 45%
@metric МОМЕНТЫ · регистрации | 1 340 | +26% | всплеск с органики
@metric МОМЕНТЫ · вторая сессия | 19% | 0 п.п. | активация не растёт

@status красный Удержание ЗАПИСОК пробило порог из 05_METRICS §4. Заведено как P0, разбор причин — в ночном цикле 14.08.

> Данные по каналам МОМЕНТОВ неполные: атрибуция органики оценочная. Точность появится после выдачи доступа к аналитике.

---

# Риски

- **Удержание ЗАПИСОК.** Гипотеза CPO: после релиза 8 августа заметка требует лишних двух действий на сохранение. Проверяется на записи сессий, ответ к 14.08.
- **Ёмкость разработки.** Перенос биллинга съедает всю неделю CTO. Всё остальное по платформе стоит.
- **Атрибуция МОМЕНТОВ.** Рост в 26% нечем объяснить. Решение принимать по нему нельзя, пока нет доступа к аналитике.
- **Юридический.** Оферта в редакции 3 требует подписи учредителя, до этого публиковать нельзя.

@status жёлтый InfoSec: инцидентов нет, доступы за неделю не расширялись.

---

# Портфельный приоритет

## На 4 дня вперёд, единым списком по всему портфелю

| Приоритет | Задача | Продукт | Владелец |
|---|---|---|---|
| P0 | Разбор падения удержания и откат лишних шагов | ЗАПИСКИ | PO-ЗАПИСКИ |
| P1 | Перенос биллинга на единый аккаунт | Платформа | CTO |
| P1 | Экран второй сессии: убрать пустой старт | МОМЕНТЫ | PO-МОМЕНТЫ |
| P2 | Импорт клиентов из таблицы | ПРАКТИКА | PO-ПРАКТИКА |
| P3 | Карточка клиента: сводка динамики | ПРАКТИКА | CPO |

Вытеснено из плана: онбординг специалиста версии 2 и подготовка ЛИСТОВ к печати. Причина — ёмкость разработки занята биллингом, растягивать спринт COO не даёт.

> Приоритет считался по формуле из устава §9. Спорных позиций нет, вето не заявлено.

---

# Бюджет

## Август, факт на 13-е число

@metric Расход месяца | 214 000 ₽ | +6% к плану | инфраструктура выросла
@metric План месяца | 202 000 ₽ | | утверждён 4 августа
@metric Кэш на счёте | 1 840 000 ₽ | −214 000 ₽ | месячный расход
@metric Запас хода | 8,6 мес | −0,4 мес | при текущем темпе

- Превышение на 12 000 ₽ — хостинг МОМЕНТОВ после всплеска регистраций. Расход обоснован, CFO не возражает.
- Маркетинговый бюджет израсходован на 38%, остаток идёт на сентябрьский контент.
- Незакрытых актов за июль: два, на 47 000 ₽. Бухгалтер запросил документы у подрядчиков.

@status зелёный Бюджет под контролем, вето CFO не заявлено.

---

# Задачи учредителю

## Очередь на 13 августа, отсортирована по блокирующей силе

@human Выдать токен доступа к аналитике МОМЕНТОВ по инструкции в 11_HUMAN_QUEUE. Без него PO работают вслепую по всей органике. 10 минут.
@human Подписать оферту в редакции 3. Публикация и приём новых оплат заблокированы до подписи. Юрист согласовал, правок нет. 20 минут.
@human Решить по цене тарифа ПРАКТИКА+ на сентябрь. Тип 1, решает только учредитель. Рекомендация CFO: не поднимать до стабилизации удержания ЗАПИСОК. 15 минут.

Открытых задач в очереди: 3 из 5 допустимых. Просроченных нет.

> Следующий борд — понедельник 17 августа, 10:00. Ночные циклы идут ежедневно в 05:00.
```

---

## 9. Чек-лист перед отправкой

1. Секреты на месте, значения нигде не напечатаны.
2. Сводка — не более 15 строк, дек — от 5 до 9 слайдов (`00_ORG_CHARTER §5`).
3. Ни одной выдуманной цифры: нет данных — так и написано.
4. В деке нет эмодзи, кроме ⚠ и статусных точек; в сообщении — кроме ⚠️ и ✓.
5. Нет стоп-слов бренда: осознанность, трансформация, ресурсное состояние, гармония, исцеление, прокачать, умный алгоритм (`OKOEM_Brand_Platform §7`).
6. Каждая задача учредителю содержит глагол, причину, что заблокировано и оценку времени (`§6.4`).
7. Дек открыт глазами хотя бы на первом слайде: ничего не обрезано, таблицы не разъехались.
8. Имя файла по разделу 6.
9. Код выхода `tg_send.py` равен 0. Иначе — раздел 7.
