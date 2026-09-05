"""v4.0 §14.4.1 — разбиение L1 SOURCE на окна обработки.

Задача одна: покрыть источник ЦЕЛИКОМ. Semantic-v1 отправлял модели
`text[:4000]` и молчал об остальном — документ на двадцать страниц давал
знания только с первой, и отличить «в тексте больше ничего нет» от «мы
туда не смотрели» было нечем. §14.23 называет это нарушением прямо.

Порядок разбиения — по структуре, а не по длине (§14.4.1):

1. заголовки Markdown, если они есть (L1 SOURCE их сохраняет);
2. группы абзацев внутри раздела;
3. предложения — если один абзац сам длиннее окна;
4. строки — если предложение длиннее (владелец, 05.09.2026);
5. жёсткая резка по границе символа — если длиннее и строка.

Четвёртый уровень добавлен по замеру: медицинский бланк и таблица не
имеют ни пустых строк, ни конечной пунктуации, поэтому уровни 2 и 3 на
них не срабатывают вовсе, и такой абзац сразу проваливался в жёсткую
резку — она рвёт строку таблицы посередине значения. Строка же —
естественная единица бланка, и делится он по ней без потерь.

Пятый уровень существует не для красоты: без него абзац без единой
точки и без переводов строки (расшифровка звука одним куском) не
поместился бы ни в одно окно, и его пришлось бы либо отбросить, либо
отдать модели целиком. Оба варианта — то самое молчаливое усечение.

Окно НЕ пересекается с соседними. §14.4.1 разрешает ограниченное
перекрытие «where context is needed», но не требует его, а перекрытие
ломает простой учёт покрытия: один и тот же текст попадал бы в две
статистики. Понадобится контекст — вводить осознанно и отдельным
решением, а не заранее.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

#: Верхняя граница окна в символах. Не «сколько влезет в модель» — это
#: свойство конкретной модели, а её выбирает R4. Здесь граница структуры
#: разбора, одинаковая для всех моделей; окно, не влезающее в выбранную
#: модель, делится тем же `split_window()`, что и переполненное атомами.
WINDOW_MAX_CHARS = 3000

#: Ниже этого окна не дробятся: осколок в пару строк не несёт контекста,
#: и извлекать из него нечего, кроме шума.
WINDOW_MIN_CHARS = 200

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class SemanticWindow:
    """Кусок источника со своим местом в нём.

    `char_start`/`char_end` — смещения в ИСХОДНОМ тексте, не в куске:
    из них собирается происхождение упоминания (§14.5), и пересчитать их
    задним числом уже нельзя.
    """

    ordinal: int
    text: str
    char_start: int
    char_end: int
    #: Путь заголовков до этого места («Анализы» → «Биохимия»). Идёт в
    #: промпт как контекст: без него абзац «показатель в норме» не
    #: сообщает, какой именно показатель.
    heading_path: tuple[str, ...]

    @property
    def text_hash(self) -> str:
        """§14.4.1: у окна есть `text_hash`. По нему повторный разбор
        того же участка узнаётся без сравнения текстов, а расхождение
        «в базе записано одно, в файле другое» становится видимым."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def _sections(text: str) -> list[tuple[tuple[str, ...], int, int]]:
    """Разделы по заголовкам Markdown: (путь заголовков, начало, конец).

    Заголовки вложены, поэтому путь накапливается по уровням: `##
    Анализы` внутри `# Приём` даёт ('Приём', 'Анализы'). Текст до первого
    заголовка — раздел с пустым путём, а не потерянный кусок.
    """
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [((), 0, len(text))]

    sections: list[tuple[tuple[str, ...], int, int]] = []
    if matches[0].start() > 0:
        sections.append(((), 0, matches[0].start()))

    stack: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        # Раздел начинается НА заголовке, а не после него: строка
        # заголовка — такой же текст источника, и оставить её вне всех
        # окон значило бы завести исключение в инварианте «каждый символ
        # ровно в одном окне». Заголовок при этом ещё и дублируется в
        # `heading_path` — там он контекст для модели, здесь просто
        # покрытый текст.
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((tuple(t for _, t in stack), start, end))
    return sections


def _split_long(text: str, start: int, limit: int,
                *, hard_cut: bool = True) -> list[tuple[int, int]]:
    """Границы кусков не длиннее `limit` внутри одного абзаца.

    Сначала по предложениям, потом по строкам, и только если строка сама
    длиннее — жёстко по символам. Возвращаются смещения в исходном
    тексте, чтобы привязка к источнику не терялась ни на одном уровне
    дробления.
    """
    pieces: list[tuple[int, int]] = []
    cursor = 0
    for part in _SENTENCE_END.split(text):
        if not part:
            continue
        offset = text.index(part, cursor)
        cursor = offset + len(part)
        if len(part) <= limit:
            pieces.append((start + offset, start + offset + len(part)))
            continue
        pieces.extend(_split_lines(part, start + offset, limit, hard_cut=hard_cut))
    return pieces


def _split_lines(text: str, start: int, limit: int,
                 *, hard_cut: bool = True) -> list[tuple[int, int]]:
    """Границы по строкам; жёсткая резка — только для строки длиннее `limit`.

    `hard_cut=False` — резать по символам нельзя, слишком длинная строка
    возвращается целиком. Нужно пути таймаута: окно там не нарезается на
    спаны, а уходит в модель отдельными кусками, и кусок, оборванный
    посередине слова, даст не «меньше знаний», а мусор. Контракт
    покрытия требует в таком случае явного провала, не тихой порчи.

    Смещения ведутся бегущим курсором, а не поиском подстроки: у таблицы
    строки повторяются дословно («Норма | —»), и `index()` нашёл бы
    первую вместо текущей, сдвинув привязку к источнику.
    """
    pieces: list[tuple[int, int]] = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        offset, cursor = cursor, cursor + len(line)
        if not line.strip():
            continue
        if len(line) <= limit or not hard_cut:
            pieces.append((start + offset, start + offset + len(line)))
            continue
        for begin in range(0, len(line), limit):
            piece = line[begin:begin + limit]
            pieces.append((start + offset + begin, start + offset + begin + len(piece)))
    return pieces


def split_text(text: str, *, limit: int, hard_cut: bool = True) -> list[str]:
    """Тот же порядок деления, что и у окон, но результат — куски текста.

    Нужен пути таймаута в `semantic_extract`: до 05.09.2026 он делил
    своим, более слабым способом (абзацы, затем предложения) и на
    плотной таблице не мог поделить вовсе — три источника корпуса
    владельца так и не прошли R8. Разных механизма деления быть не
    должно; здесь переиспользуется тот же.

    Куски собираются обратно `_pack()` — без этого таблица на сорок
    строк дала бы сорок отдельных вызовов модели по одной строке, где ни
    одна не несёт контекста соседней. Деление сверху вниз, сборка снизу
    вверх — ровно как у окон.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for paragraph in _PARAGRAPH_BREAK.split(text):
        if not paragraph.strip():
            cursor += len(paragraph)
            continue
        offset = text.index(paragraph, cursor)
        cursor = offset + len(paragraph)
        if len(paragraph) <= limit:
            spans.append((offset, offset + len(paragraph)))
        else:
            spans.extend(_split_long(paragraph, offset, limit, hard_cut=hard_cut))
    return [piece for piece in (text[a:b].strip() for a, b in _pack(spans, limit)) if piece]


def _pack(spans: list[tuple[int, int]], limit: int) -> list[tuple[int, int]]:
    """Склеить подряд идущие куски, пока помещаются в окно.

    Именно здесь появляются «группы абзацев» из §14.4.1: разбиение идёт
    сверху вниз, а сборка — снизу вверх, и окно получается настолько
    большим, насколько позволяет структура, а не настолько мелким,
    насколько получилось разрезать.
    """
    packed: list[tuple[int, int]] = []
    for span in spans:
        if packed and span[1] - packed[-1][0] <= limit:
            packed[-1] = (packed[-1][0], span[1])
        else:
            packed.append(span)
    return packed


def build_windows(text: str, *, limit: int = WINDOW_MAX_CHARS) -> list[SemanticWindow]:
    """Окна по ВСЕМУ тексту, по порядку, без пересечений и без пропусков.

    Инвариант, который держит тест: любой непробельный символ источника
    попадает ровно в одно окно. Он и есть содержание §14.4.1 — «никакой
    немой `text[:4000]` отсечки».
    """
    windows: list[SemanticWindow] = []
    for heading_path, section_start, section_end in _sections(text):
        section = text[section_start:section_end]
        if not section.strip():
            continue

        spans: list[tuple[int, int]] = []
        cursor = 0
        for paragraph in _PARAGRAPH_BREAK.split(section):
            if not paragraph.strip():
                cursor += len(paragraph)
                continue
            offset = section.index(paragraph, cursor)
            cursor = offset + len(paragraph)
            begin = section_start + offset
            if len(paragraph) <= limit:
                spans.append((begin, begin + len(paragraph)))
            else:
                spans.extend(_split_long(paragraph, begin, limit))

        for start, end in _pack(spans, limit):
            piece = text[start:end]
            if not piece.strip():
                continue
            windows.append(SemanticWindow(
                ordinal=len(windows), text=piece, char_start=start, char_end=end,
                heading_path=heading_path))
    return windows


def split_window(window: SemanticWindow, *, parts: int = 2) -> list[SemanticWindow]:
    """Разделить окно, упёршееся в потолок атомов (§14.4.1 TRUNCATED).

    Спека требует «автоматически делится/перезапускается; молча отбросить
    остальные atoms нельзя». Деление идёт по той же структуре, что и
    первичное разбиение, — просто с вдвое меньшим пределом, поэтому
    границы снова попадают на абзацы и предложения, а не в середину
    слова.

    Окно, которое уже нельзя осмысленно разделить (короче двух
    минимальных), возвращается как есть: вызывающий обязан отличить
    «поделили» от «делить нечего» и во втором случае не зациклиться.
    """
    if len(window.text) < WINDOW_MIN_CHARS * 2:
        return [window]

    limit = max(WINDOW_MIN_CHARS, len(window.text) // max(parts, 2))
    children = build_windows(window.text, limit=limit)
    if len(children) < 2:
        return [window]
    return [
        SemanticWindow(
            ordinal=index,
            text=child.text,
            char_start=window.char_start + child.char_start,
            char_end=window.char_start + child.char_end,
            heading_path=window.heading_path + child.heading_path,
        )
        for index, child in enumerate(children)
    ]
