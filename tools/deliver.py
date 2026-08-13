#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ОКОЁМ · доставка результата цикла учредителю одной командой.

Что делает: собирает дек из исходника, рендерит его в PDF, отправляет в
Telegram сначала текст, потом презентацию. Ровно в таком порядке — в ленте
учредителя сперва появляется смысл, следом вложение.

Зачем одна команда вместо трёх. Раньше доставка была цепочкой ручных вызовов
make_deck → tg_send, и ночной цикл присылал только текст: собрать дек стоило
отдельного шага, который в протоколе не был обязательным. Требование
изменилось — оба ритма, и ночной цикл, и борд, обязаны присылать текст и
готовую презентацию. Одна команда делает это невозможным забыть.

Запуск:
    # ночной цикл: текст плюс дек
    python3 tools/deliver.py --text outbox/2026-08-14_daily.txt \\
                             --deck daily/daily_2026-08-14.md

    # борд: то же плюс HTML-версия дека для большого экрана
    python3 tools/deliver.py --text outbox/2026-08-17_board.txt \\
                             --deck board/board_2026-08-17_N1.md --with-html

    # проверка без отправки: собирает файлы, показывает что ушло бы
    python3 tools/deliver.py --text ... --deck ... --dry-run

Коды выхода: 0 — доставлено, 1 — не доставлено (причина в stderr).
Переменные окружения — те же, что у tg_send.py: TG_BOT_TOKEN, TG_CHAT_ID.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deck_lint    # noqa: E402
import deck_to_pdf  # noqa: E402
import make_deck    # noqa: E402
import tg_send      # noqa: E402


def log(message):
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def build_caption(meta, slides_count, fallback_name):
    """
    Подпись к презентации собираем из мета-строк дека, а не из имени файла:
    учредитель видит «Борд C-1 № 7 · 17 августа 2026 · 6 слайдов», а не
    board_2026-08-17_N7.pdf.
    """
    title = (meta.get("title") or "").strip()
    date = (meta.get("date") or "").strip()
    meeting = (meta.get("meeting") or "").strip()

    parts = [title or fallback_name]
    # Номер заседания добавляем, только если его нет в заголовке: составители
    # деков пишут «Борд C-1 · заседание N7» и в @title, и в @meeting.
    if meeting and ("N" + meeting) not in title and ("№ " + meeting) not in title:
        parts.append("№ %s" % meeting)
    if date:
        parts.append(date)
    parts.append(plural_slides(slides_count))
    return " · ".join(parts)


def plural_slides(n):
    """Русские окончания: 1 слайд, 2 слайда, 5 слайдов."""
    if n % 10 == 1 and n % 100 != 11:
        word = "слайд"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        word = "слайда"
    else:
        word = "слайдов"
    return "%d %s" % (n, word)


def build_deck(source, browser=None, strict=False):
    """
    Исходник дека → (html, pdf, число слайдов, подпись).

    HTML и PDF кладём рядом с исходником: имя задаёт устав (06_DELIVERY §6),
    и придумывать его здесь заново — верный способ разойтись с документом.
    """
    if not os.path.isfile(source):
        raise SystemExit("ОШИБКА: исходник дека не найден: %s" % source)

    with open(source, "r", encoding="utf-8") as handle:
        text = handle.read()

    meta, slides = make_deck.parse_deck(text)
    if not slides:
        raise SystemExit("ОШИБКА: в исходнике дека нет ни одного слайда: %s" % source)

    # Проверка стандарта идёт до сборки: замечания видно, пока дек ещё можно
    # переписать, а не после того, как он ушёл учредителю.
    findings = deck_lint.lint(source)
    errors = [f for f in findings if f.level == "ошибка"]
    if findings:
        log("Проверка по 10_DECK_STYLE — ошибок %d, замечаний %d:"
            % (len(errors), len(findings) - len(errors)))
        for finding in findings:
            log(finding.render())
    if errors and strict:
        raise SystemExit("ОШИБКА: дек не проходит проверку стандарта, доставка "
                         "остановлена (--strict). Исправьте ошибки выше.")

    stem = os.path.splitext(source)[0]
    html_path = stem + ".html"
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(make_deck.build_html(meta, slides))
    log("дек собран: %s — %s" % (html_path, plural_slides(len(slides))))

    pdf_path = stem + ".pdf"
    try:
        _, pages, size = deck_to_pdf.render(html_path, pdf_path, browser=browser)
    except deck_to_pdf.RenderError as exc:
        log("ОШИБКА сборки PDF: %s" % exc)
        return html_path, None, len(slides), build_caption(meta, len(slides),
                                                           os.path.basename(stem))

    log("презентация: %s — страниц %d, %.1f КБ" % (pdf_path, pages, size / 1024.0))
    if pages != len(slides):
        # Не отказ в доставке, но повод посмотреть глазами: слайд мог перетечь
        # на вторую страницу, а мог и потеряться.
        log("ВНИМАНИЕ: страниц %d при %d слайдах — проверьте вёрстку дека."
            % (pages, len(slides)))

    caption = build_caption(meta, len(slides), os.path.basename(stem))
    return html_path, pdf_path, len(slides), caption


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="deliver.py",
        description="ОКОЁМ · доставка учредителю: текст плюс готовая презентация.",
    )
    parser.add_argument("--text", required=True,
                        help="файл с текстом сообщения (разметка HTML по шаблону 06_DELIVERY)")
    parser.add_argument("--deck", default=None,
                        help="исходник дека (.md); без него уйдёт только текст")
    parser.add_argument("--pdf", default=None,
                        help="готовая презентация: отправить как есть, не собирая. "
                             "Так доставляют, когда рендерить негде — например с раннера GitHub")
    parser.add_argument("--with-html", action="store_true",
                        help="дослать HTML-версию дека (интерактивная, для большого экрана)")
    parser.add_argument("--caption", default=None,
                        help="перебить подпись к презентации")
    parser.add_argument("--browser", default=None, help="путь к chromium/chrome")
    parser.add_argument("--strict", action="store_true",
                        help="не отправлять дек, пока он не пройдёт проверку 10_DECK_STYLE")
    parser.add_argument("--chat-id", default=None, help="перебить TG_CHAT_ID")
    parser.add_argument("--silent", action="store_true", help="доставить без звука")
    parser.add_argument("--dry-run", action="store_true",
                        help="собрать файлы, но ничего не отправлять")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.text):
        log("ОШИБКА: файл текста не найден: %s" % args.text)
        return 1
    if os.path.getsize(args.text) == 0:
        log("ОШИБКА: файл текста пуст: %s" % args.text)
        return 1

    html_path = pdf_path = caption = None
    if args.pdf:
        # Готовый PDF: собирать нечего, проверять стандарт поздно — дек уже
        # собран там, где есть браузер.
        if not os.path.isfile(args.pdf):
            log("ОШИБКА: презентация не найдена: %s" % args.pdf)
            return 1
        pdf_path = args.pdf
        caption = os.path.splitext(os.path.basename(args.pdf))[0]
    elif args.deck:
        html_path, pdf_path, _, caption = build_deck(args.deck, browser=args.browser,
                                                      strict=args.strict)
    caption = args.caption or caption

    common = []
    if args.chat_id:
        common += ["--chat-id", args.chat_id]
    if args.silent:
        common.append("--silent")
    if args.dry_run:
        common.append("--dry-run")

    # 1. Текст. Шаблоны сводки и сообщения борда идут с разметкой — отсюда --html.
    code = tg_send.main(common + ["text", "--from-file", args.text, "--html", "--no-preview"])
    if code != 0:
        log("ОШИБКА: текст не доставлен, презентацию не отправляем — "
            "вложение без сопроводительного сообщения читается как мусор.")
        return 1

    # 2. Презентация.
    if pdf_path:
        code = tg_send.main(common + ["file", pdf_path, "--caption", caption or ""])
        if code != 0:
            log("ОШИБКА: презентация не доставлена. Текст ушёл, PDF лежит здесь: %s" % pdf_path)
            return 1
    elif args.deck:
        # PDF собрать не вышло — HTML лучше, чем ничего: дек всё равно должен уйти.
        log("PDF собрать не удалось — отправляем HTML-версию дека.")
        code = tg_send.main(common + ["file", html_path, "--caption", caption or ""])
        if code != 0:
            log("ОШИБКА: дек не доставлен ни в одном формате.")
            return 1

    # 3. HTML вдогонку — по требованию.
    if args.with_html and html_path and pdf_path:
        code = tg_send.main(common + ["file", html_path,
                                      "--caption", "Тот же дек в HTML — открывается в браузере"])
        if code != 0:
            log("ВНИМАНИЕ: HTML-версия не доставлена. Главное — текст и PDF — ушло.")

    log("готово: доставлено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
