#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ОКОЁМ · превращение HTML-дека в PDF-презентацию.

Зачем отдельный формат. HTML-дек хорош на ноутбуке: он интерактивный и
самодостаточный. Но в Telegram HTML приходит вложением, которое телефон не
открывает предпросмотром, — учредителю приходится скачивать файл и искать,
чем его открыть. PDF Telegram листает прямо в чате, страница за страницей.
Поэтому дек уходит двумя файлами: PDF — чтобы посмотреть сразу, HTML — чтобы
открыть на большом экране.

Рендерит headless-браузером на движке Chromium. Никаких сторонних библиотек:
браузер и так стоит в окружении, а тянуть в проект weasyprint или wkhtmltopdf
ради одной операции — лишняя зависимость, которая рано или поздно отвалится.

Страницу открываем с ?print=1: в этом режиме дек сам раскладывается в
фиксированный слайд-бокс 16:9 и ужимает содержимое, чтобы слайд занял ровно
одну страницу (см. fitSlides в make_deck.py).

Запуск:
    python3 deck_to_pdf.py board/board_2026-08-17_N7.html
    python3 deck_to_pdf.py deck.html -o deck.pdf
    python3 deck_to_pdf.py deck.html --browser /usr/bin/google-chrome
"""

import argparse
import glob
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import urllib.request

# Кандидаты на роль браузера, в порядке предпочтения. Список закрывает три
# среды, в которых это реально запускается: контейнер Claude Code (Playwright),
# раннер GitHub Actions (google-chrome предустановлен) и обычный Linux.
BROWSER_CANDIDATES = [
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
]

# Playwright кладёт браузеры не в PATH, а в свой каталог.
BROWSER_GLOBS = [
    "/opt/pw-browsers/chromium-*/chrome-linux/chrome",
    "/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell",
    "/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
    "/usr/lib/chromium/chromium",
]

RENDER_TIMEOUT = 180  # секунд: дек маленький, но холодный старт браузера медленный


def log(message):
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


class RenderError(Exception):
    """Отрендерить не удалось; сообщение объясняет, что делать."""


def find_browser(explicit=None):
    """Ищем движок: явный путь → PATH → каталоги Playwright."""
    if explicit:
        if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
            return explicit
        raise RenderError("указанный браузер не найден или не исполняем: %s" % explicit)

    for name in BROWSER_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found

    for pattern in BROWSER_GLOBS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]  # свежая версия, если их несколько

    raise RenderError(
        "не найден браузер для сборки PDF.\n"
        "Установите chromium (apt-get install -y chromium) или укажите путь:\n"
        "  python3 deck_to_pdf.py deck.html --browser /usr/bin/google-chrome\n"
        "Без PDF дек всё равно можно доставить: отправьте HTML файлом."
    )


def page_count(pdf_bytes):
    """
    Сколько страниц получилось. Считаем объекты /Type /Page, не трогая /Pages.

    Нужно не для красоты: это единственная дешёвая проверка, что рендер не
    выдал пустышку или, наоборот, не расползся вдвое против числа слайдов.
    """
    count = 0
    index = 0
    needle = b"/Type"
    while True:
        index = pdf_bytes.find(needle, index)
        if index == -1:
            break
        index += len(needle)
        tail = pdf_bytes[index:index + 12].lstrip()
        if tail.startswith(b"/Page") and not tail.startswith(b"/Pages"):
            count += 1
    return count


def _kill_tree(process):
    """
    Убить браузер вместе со всем выводком.

    Chrome — это не один процесс, а дерево: zygote, gpu, renderer. Убийство
    только головного оставляет детей жить, а вместе с ними — открытые концы
    наших пайпов. Поэтому запускаем браузер в собственной группе процессов
    (start_new_session) и здесь бьём по всей группе.
    """
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except OSError:
            pass


def run_browser(command, timeout):
    """
    Запустить браузер и дождаться его, не рискуя повиснуть навсегда.

    Почему не subprocess.run(timeout=...): по таймауту он убивает только
    головной процесс, а затем снова ждёт закрытия пайпов — которые держат
    выжившие потомки Chrome. Ожидание не кончается никогда, и таймаут,
    написанный ради защиты от зависания, сам зависает. Проверено на раннере
    GitHub: шаг сборки PDF шёл больше четырёх минут при таймауте в три.
    """
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _, stderr = process.communicate(timeout=timeout)
        return process.returncode, stderr
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            # Пайпы всё ещё кем-то держатся. Дальше ждать нечего: процесс
            # группы убит, а висящие дескрипторы закроет выход из программы.
            pass
        raise RenderError("браузер не уложился в %d с и был снят" % timeout)


def render(html_path, pdf_path, browser=None, timeout=RENDER_TIMEOUT):
    """Гоним HTML через headless-браузер и кладём рядом PDF."""
    html_path = os.path.abspath(html_path)
    if not os.path.isfile(html_path):
        raise RenderError("входной файл не найден: %s" % html_path)

    binary = find_browser(browser)
    pdf_path = os.path.abspath(pdf_path)
    url = urllib.request.pathname2url(html_path)

    # Отдельный профиль на запуск: иначе браузер цепляется за чужой каталог
    # и падает с «profile appears to be in use».
    profile = tempfile.mkdtemp(prefix="okoem-deck-")
    try:
        command = [
            binary,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",              # контейнеры почти всегда без user namespaces
            "--disable-dev-shm-usage",   # /dev/shm в контейнерах мал, браузер падает
            "--no-first-run",
            "--disable-extensions",
            "--disable-background-networking",  # без походов в сеть за обновлениями
            "--no-pdf-header-footer",    # без колонтитулов с URL и датой
            # Флага --run-all-compositor-stages-before-draw здесь намеренно нет:
            # без настоящего композитора он ждёт стадии отрисовки, которая не
            # наступает, и печать не заканчивается никогда. Времени на подгонку
            # масштаба хватает и одного бюджета виртуального времени.
            "--virtual-time-budget=5000",
            "--user-data-dir=%s" % profile,
            "--print-to-pdf=%s" % pdf_path,
            "file://%s?print=1" % url,
        ]
        returncode, stderr = run_browser(command, timeout)

        if not os.path.isfile(pdf_path) or os.path.getsize(pdf_path) == 0:
            # У Chromium болтливый stderr (dbus, gpu) — в ошибку тащим только хвост.
            tail = stderr.decode("utf-8", "replace").strip().splitlines()[-4:]
            raise RenderError("браузер не создал PDF (код %s).\n%s"
                              % (returncode, "\n".join(tail)))
    finally:
        shutil.rmtree(profile, ignore_errors=True)

    with open(pdf_path, "rb") as handle:
        data = handle.read()
    if not data.startswith(b"%PDF"):
        raise RenderError("на выходе не PDF: %s" % pdf_path)

    return pdf_path, page_count(data), len(data)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="deck_to_pdf.py",
        description="ОКОЁМ · сборка PDF-презентации из HTML-дека.",
    )
    parser.add_argument("input", help="входной HTML-дек")
    parser.add_argument("-o", "--output", default=None,
                        help="выходной PDF (по умолчанию — рядом со входным)")
    parser.add_argument("--browser", default=None,
                        help="путь к chromium/chrome, если он не в PATH")
    parser.add_argument("--expect-pages", type=int, default=None,
                        help="ожидаемое число страниц; расхождение — ненулевой код возврата")
    args = parser.parse_args(argv)

    output = args.output or os.path.splitext(args.input)[0] + ".pdf"
    try:
        path, pages, size = render(args.input, output, browser=args.browser)
    except RenderError as exc:
        log("ОШИБКА: %s" % exc)
        return 1

    log("готово: %s — страниц %d, размер %.1f КБ" % (path, pages, size / 1024.0))

    if args.expect_pages is not None and pages != args.expect_pages:
        log("ВНИМАНИЕ: страниц %d, ожидалось %d — слайд перетёк на вторую страницу "
            "или потерялся. Проверьте дек глазами перед отправкой." % (pages, args.expect_pages))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
