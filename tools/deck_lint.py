#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ОКОЁМ · проверка дека на соответствие стандарту `charter/10_DECK_STYLE.md`.

Что проверяет: заголовки-темы вместо заголовков-тезисов, обороты-пустышки,
слишком длинные предложения, слайды без единого доказательства, отсутствие
последнего слайда с решениями, эмодзи.

Чего не проверяет: верна ли мысль и есть ли она вообще. Это остаётся на
авторе, и никакая проверка этого не заменит. Линтер ловит форму, в которой
отсутствие мысли обычно и прячется.

Намеренно не умеет переписывать текст. Дек, исправленный автозаменой слов,
остаётся деком без мысли — только с другими словами.

Запуск:
    python3 tools/deck_lint.py board/board_2026-08-17_N1.md
    python3 tools/deck_lint.py deck.md --strict   # замечания тоже валят проверку

Коды выхода: 0 — чисто (или только замечания), 1 — есть ошибки.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import make_deck  # noqa: E402

# Обороты, которые не несут смысла и выдают текст, написанный ради объёма.
# Список из 10_DECK_STYLE §6; правится там и здесь одновременно.
EMPTY_PHRASES = [
    "важно отметить", "стоит отметить", "стоит подчеркнуть", "необходимо учитывать",
    "следует отметить", "в современном мире", "играет важную роль", "является ключевым",
    "ключевым фактором", "комплексный подход", "в рамках данного", "в рамках которого",
    "представляет собой", "не только, но и", "динамично развивающийся",
    "инновационный", "уникальное решение", "эффективное решение", "синергия",
    "драйвер роста", "бесшовный", "экосистемный", "в целях повышения",
    "с целью оптимизации", "оптимизация процессов",
]

# Запреты бренда — устав §10. Метрика, выросшая за счёт их нарушения,
# считается не выросшей; в деке они не появляются тем более.
BRAND_BANNED = [
    "осознанность", "гармония", "ресурсное состояние", "трансформация",
    "исцеление", "прокачать", "умный алгоритм",
]

# Отглагольные существительные: почти всегда прячут действие, которое лучше
# назвать глаголом. Проверяем самые частые.
NOMINAL = ["осуществляется", "производится", "выполняется проверка", "имеет место"]

# Заголовки, которые являются оглавлением, а не мыслью.
TOPIC_TITLES = {
    "метрики", "риски", "продукты", "статус", "итоги", "обзор", "введение",
    "заключение", "повестка", "о нас", "о компании", "результаты", "выводы",
    "числа", "три числа", "приоритеты", "план", "бэклог", "разное",
}

MAX_SENTENCE_WORDS = 25
MAX_BULLETS = 6
MAX_BULLET_WORDS = 22
MIN_TITLE_WORDS = 4      # либо столько слов, либо число в заголовке
MAX_TITLE_WORDS = 14

EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)


class Finding(object):
    def __init__(self, level, slide, message, quote=""):
        self.level = level          # "ошибка" | "замечание"
        self.slide = slide
        self.message = message
        self.quote = quote

    def render(self):
        where = "слайд %s" % self.slide if self.slide else "дек"
        line = "  [%s] %s: %s" % (self.level, where, self.message)
        if self.quote:
            line += "\n      → %s" % self.quote
        return line


def words(text):
    return [w for w in re.split(r"\s+", text.strip()) if w]


def has_digit(text):
    return bool(re.search(r"\d", text))


def slide_items(slide):
    """
    Слайд → список (вид, текст) по отдельным фрагментам.

    Именно по фрагментам, а не по слитному тексту слайда: иначе заголовок,
    строки таблицы и буллеты склеиваются в одно «предложение на сто слов»,
    и проверка длины начинает врать на каждом втором слайде.
    """
    items = [("title", slide["title"])] if slide["title"] else []
    for block in slide["blocks"]:
        for item in block["items"]:
            if isinstance(item, dict):
                # Метрика и статус — словари; склеиваем их значения в строку.
                items.append((block["kind"], " ".join(str(v) for v in item.values() if v)))
            else:
                items.append((block["kind"], str(item)))
    return [(kind, text) for kind, text in items if text and text.strip()]


def slide_text(slide):
    """Весь текст слайда одной строкой — для проверок, которым неважна структура."""
    return "\n".join(text for _, text in slide_items(slide))


def check_title(slide, number, findings):
    title = slide["title"].strip()
    if not title:
        findings.append(Finding("ошибка", number, "слайд без заголовка"))
        return

    count = len(words(title))
    if title.lower().strip(" .:—-") in TOPIC_TITLES:
        findings.append(Finding(
            "ошибка", number,
            "заголовок — тема, а не вывод. Читающий по одним заголовкам должен "
            "получить связный рассказ", title))
    elif count < MIN_TITLE_WORDS and not has_digit(title):
        findings.append(Finding(
            "ошибка", number,
            "заголовок похож на оглавление: %d слов(а) и ни одного числа. "
            "Сформулируйте вывод предложением" % count, title))
    if count > MAX_TITLE_WORDS:
        findings.append(Finding(
            "замечание", number,
            "заголовок длиннее %d слов — обычно это две мысли, которые стоит "
            "разнести на два слайда" % MAX_TITLE_WORDS, title))


# Длину предложений меряем только там, где предложения и бывают. В таблице,
# метрике и строке статуса текст намеренно телеграфный, и мерить его правилами
# прозы значит спорить с их назначением.
PROSE_KINDS = {"para", "bullets", "note", "sub", "human"}


def check_language(slide, number, findings):
    text = slide_text(slide)
    lowered = text.lower()
    for phrase in EMPTY_PHRASES:
        if phrase in lowered:
            findings.append(Finding("ошибка", number,
                                    "оборот-пустышка «%s»" % phrase))
    for phrase in BRAND_BANNED:
        if phrase in lowered:
            findings.append(Finding("ошибка", number,
                                    "слово из запрета бренда «%s» (устав §10)" % phrase))
    for phrase in NOMINAL:
        if phrase in lowered:
            findings.append(Finding("замечание", number,
                                    "отглагольное «%s» — назовите действие глаголом" % phrase))

    found = EMOJI.findall(text)
    if found:
        findings.append(Finding("ошибка", number,
                                "эмодзи в деке: %s" % " ".join(sorted(set(found)))))

    for kind, item in slide_items(slide):
        if kind not in PROSE_KINDS:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", item):
            length = len(words(sentence))
            if length > MAX_SENTENCE_WORDS:
                findings.append(Finding(
                    "замечание", number,
                    "предложение из %d слов — режьте, одно предложение это одна мысль"
                    % length, sentence.strip()[:90] + "…"))


def check_evidence(slide, number, findings):
    """
    Слайд обязан на что-то опираться: число, метрика, статус, таблица или
    честное «данных нет». Слайд из одних утверждений — это мнение.
    """
    kinds = {block["kind"] for block in slide["blocks"]}
    text = slide_text(slide)
    honest = "данных нет" in text.lower() or "нет данных" in text.lower()
    if kinds & {"metrics", "table", "status", "human"} or has_digit(text) or honest:
        return
    findings.append(Finding(
        "ошибка", number,
        "слайд ни на что не опирается: ни числа, ни метрики, ни таблицы, ни "
        "пометки «данных нет»"))


def check_bullets(slide, number, findings):
    for block in slide["blocks"]:
        if block["kind"] != "bullets":
            continue
        if len(block["items"]) > MAX_BULLETS:
            findings.append(Finding(
                "замечание", number,
                "буллетов %d при пределе %d — слайд перегружен"
                % (len(block["items"]), MAX_BULLETS)))
        for item in block["items"]:
            if len(words(item)) > MAX_BULLET_WORDS:
                findings.append(Finding(
                    "замечание", number,
                    "буллет из %d слов — это абзац" % len(words(item)),
                    item[:90] + "…"))


def check_deck(meta, slides, findings):
    if not slides:
        findings.append(Finding("ошибка", None, "в деке нет слайдов"))
        return
    if len(slides) > 9:
        findings.append(Finding("замечание", None,
                                "слайдов %d — устав держит борд в пределах 9" % len(slides)))
    if not meta.get("date"):
        findings.append(Finding("замечание", None, "не заполнена мета-строка @date"))

    # Последний слайд — решения учредителю. Дек без него не заканчивается
    # действием, а значит и не приводит к нему.
    tail = "\n".join(slide_text(s) for s in slides[-2:])
    has_human = any(b["kind"] == "human" for s in slides[-2:] for b in s["blocks"])
    if not has_human and "несостоявш" not in tail.lower():
        findings.append(Finding(
            "ошибка", None,
            "в конце дека нет слайда с решениями (@human). Дек заканчивается "
            "тем, что нужно от учредителя, либо пометкой о несостоявшемся заседании"))


def lint(path):
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    meta, slides = make_deck.parse_deck(text)

    findings = []
    check_deck(meta, slides, findings)
    for number, slide in enumerate(slides, start=1):
        check_title(slide, number, findings)
        check_language(slide, number, findings)
        check_evidence(slide, number, findings)
        check_bullets(slide, number, findings)
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="deck_lint.py",
        description="ОКОЁМ · проверка дека на стандарт 10_DECK_STYLE.",
    )
    parser.add_argument("input", help="исходник дека (.md)")
    parser.add_argument("--strict", action="store_true",
                        help="считать ошибкой и замечания тоже")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.input):
        sys.stderr.write("ОШИБКА: файл не найден: %s\n" % args.input)
        return 1

    findings = lint(args.input)
    errors = [f for f in findings if f.level == "ошибка"]
    notes = [f for f in findings if f.level == "замечание"]

    if not findings:
        print("дек чист: %s" % os.path.basename(args.input))
        return 0

    print("Проверка дека %s — ошибок %d, замечаний %d:"
          % (os.path.basename(args.input), len(errors), len(notes)))
    for finding in findings:
        print(finding.render())

    if errors or (args.strict and notes):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
