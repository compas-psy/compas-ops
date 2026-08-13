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
/* Обёртка содержимого слайда. Существует ради печати: при сборке PDF скрипт
   масштабирует именно её, чтобы слайд не перетекал на вторую страницу, а сам
   слайд-бокс остался ровно в размер страницы. */
.fit{display:flex;flex-direction:column;flex:1 1 auto;min-height:0;width:100%;
     transform-origin:top center}
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

/* --- печать: один слайд — ровно одна страница 16:9 --- */
__SLIDE_BOX__
"""

# Габариты страницы печати. 16:9 — потому что дек смотрят с телефона в Telegram
# и с ноутбука, а не читают с бумаги. Держим в одном месте: этими же числами
# оперирует подгонка масштаба в JS.
PAGE_W_MM = 280.0
PAGE_H_MM = 157.5

# Правила слайд-бокса нужны дважды: в @media print (собственно печать) и в
# screen-режиме под классом .print-fit (предпросмотр, в котором JS замеряет
# высоту содержимого). Пишем их один раз с меткой __P__ перед каждым селектором,
# затем подставляем пустую строку для печати и «.print-fit » для предпросмотра —
# так две копии не могут разъехаться.
SLIDE_BOX_RULES = """
  __P__.nav,__P__.progress{display:none !important}
  __P__.slide{
    display:flex !important;
    width:%(w)smm;height:%(h)smm;
    min-height:0;max-width:none;margin:0 auto;
    padding:11mm 13mm 8mm;
    overflow:hidden;
    page-break-after:always;break-after:page;
    page-break-inside:avoid;break-inside:avoid;
  }
  __P__.slide:last-child{page-break-after:auto;break-after:auto}
  __P__.slide-foot .foot-num{display:inline}
  __P__.metric,__P__ table,__P__.status,__P__.human{break-inside:avoid;box-shadow:none}
""" % {"w": PAGE_W_MM, "h": PAGE_H_MM}

# Печать всегда идёт в светлой палитре: PDF уходит файлом и не должен зависеть
# от того, в какой теме был браузер, который его собрал.
PRINT_LIGHT_TOKENS = """
    --bg:#faf8f5;--surface:#ffffff;--surface-2:#f4f0e9;
    --ink:#23211d;--ink-soft:#4d4841;--muted:#7b746a;--line:#e4ddd1;
    --green:#1a4d3a;--gold:#c9a961;--up:#2f6b4f;--down:#a4483c;--amber:#b8862f;
    --human-bg:#f6f1e4;--shadow:none;
"""

SLIDE_BOX_CSS = """
@page{size:%(w)smm %(h)smm;margin:0}
@media print{
  :root{%(tokens)s}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  html,body{background:#fff}
%(print_rules)s
}
/* Экранный предпросмотр печати: включается только сборщиком PDF (?print=1).
   Нужен, чтобы JS замерил высоту содержимого в том же боксе, в котором оно
   потом будет напечатано, и подогнал масштаб. */
.print-fit,.print-fit body{%(tokens)s background:#fff}
%(fit_rules)s
""" % {
    "w": PAGE_W_MM,
    "h": PAGE_H_MM,
    "tokens": PRINT_LIGHT_TOKENS,
    "print_rules": SLIDE_BOX_RULES.replace("__P__", ""),
    "fit_rules": SLIDE_BOX_RULES.replace("__P__", ".print-fit "),
}

CSS = CSS.replace("__SLIDE_BOX__", SLIDE_BOX_CSS)

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

  // --- подгонка слайда под страницу ---
  // Слайд при печати обязан занимать ровно одну страницу: дек уходит в Telegram
  // файлом, и слайд, перетёкший на вторую страницу, читается как брак вёрстки.
  // Содержимое, которое не помещается, ужимаем масштабом — обрезать нельзя.
  function fitSlides(){
    slides.forEach(function(s){
      var fit = s.querySelector('.fit');
      if (!fit) return;
      fit.style.transform = '';
      var cs = window.getComputedStyle(s);
      var avail = s.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
      var need = fit.scrollHeight;
      if (need > avail && avail > 0) {
        // 0.99 — запас на округление подпикселей, ниже 0.5 не опускаемся:
        // такой слайд надо не ужимать, а разбивать на два.
        fit.style.transform = 'scale(' + Math.max(0.5, (avail / need) * 0.99) + ')';
      }
    });
  }

  function enterPrintLayout(){
    document.documentElement.classList.add('print-fit');
    fitSlides();
  }

  // Режим сборки PDF: страницу открывает tools/deck_to_pdf.py с ?print=1.
  if (/[?&]print=1/.test(location.search)) {
    enterPrintLayout();
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function(){ fitSlides(); window.deckPrintReady = true; });
    } else {
      window.deckPrintReady = true;
    }
  }

  // Печать из браузера вручную (Ctrl+P) — тот же расчёт.
  window.addEventListener('beforeprint', enterPrintLayout);
  window.addEventListener('afterprint', function(){
    document.documentElement.classList.remove('print-fit');
    slides.forEach(function(s){
      var fit = s.querySelector('.fit');
      if (fit) fit.style.transform = '';
    });
  });
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
            '<section class="slide" id="s%d"><div class="fit">%s%s'
            '<div class="slide-body">%s</div>%s</div></section>'
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
