"""Компактный форматтер ответа (контракт владельца 05.09.2026).

Отдельный модуль, а не метод `DoctorsAnswer`, по одной причине: правила
изложения и правила доказательства меняются с разной скоростью и по
разным поводам. `query_router` отвечает за то, ЧТО доказано;
этот файл — только за то, КАК это сказано. Модель тут не участвует
вовсе: текст детерминирован, один и тот же ответ на один и тот же вход.

Контракт, дословно из распоряжения:

- прямой ответ первым, 1–4 коротких предложения;
- запрещено: рассказывать, как система искала; перечислять ограничения
  поиска; слова «документ/чанк/уверенность/граф/evidence», если о них не
  спрашивали; повторять вопрос; длинные вступления; «возможно/вероятно/
  похоже»; заполнять отсутствие ответа общими рассуждениями;
- нет доказанного ответа — сказать прямо и коротко.

Для вопроса о врачах порядок задан отдельно: СНАЧАЛА специальности,
ФИО и даты — вторым планом. Перечисление документов, исследований и
диагнозов — провал приёмки, поэтому их тут просто неоткуда взять:
на вход подаётся уже собранный `DoctorsAnswer`, в котором ничего этого
нет.
"""

from __future__ import annotations

from .query_router import DoctorItem, DoctorsAnswer

#: Единственная форма отказа. Одна строка, без объяснений, почему не
#: нашлось: объяснение — это и есть «перечислять ограничения поиска».
NOT_FOUND = "Не нашёл в ваших данных подтверждённого ответа."

#: Дальше какого числа врачей поимённый список перестаёт быть коротким
#: ответом и становится перечислением. Пять — это ровно тот размер, на
#: котором Z1 в production уже читался как свалка (§1.1 отчёта
#: PRODUCTION_ANSWERS_RCA_2026-09-05.md).
_MAX_NAMED = 5


def _plural_doctors(count: int) -> str:
    """«1 врач», «2 врача», «5 врачей» — по последним цифрам числа."""
    tail_two = count % 100
    tail_one = count % 10
    if 11 <= tail_two <= 14 or tail_one == 0 or tail_one >= 5:
        return "врачей"
    if tail_one == 1:
        return "врач"
    return "врача"


def _named(item: DoctorItem) -> str:
    parts = [item.person]
    if item.specialties:
        parts.append(", ".join(item.specialties))
    if item.dates:
        parts.append(", ".join(item.dates))
    return " — ".join(parts) if len(parts) > 1 else parts[0]


def format_doctors(answer: DoctorsAnswer) -> str:
    """Текст ответа на «каких врачей я посещал».

    Врач с доказанной врачебной ролью, но без подтверждённой
    специальности, обязан присутствовать в ответе — он доказанный врач,
    просто без специальности. Молча выкинуть его из перечня
    специальностей и не сказать о нём ни слова значило бы ответить
    «двое» там, где доказано трое (дефект из распоряжения 05.09.2026).
    """
    if not answer.items:
        return NOT_FOUND

    specialties: list[str] = []
    for item in answer.items:
        for specialty in item.specialties:
            if specialty not in specialties:
                specialties.append(specialty)
    specialties.sort()

    unnamed = sum(1 for item in answer.items if not item.specialties)

    lines: list[str] = []
    if specialties:
        head = ", ".join(specialties)
        lines.append(head[0].upper() + head[1:] + ".")
    if unnamed:
        prefix = "Ещё" if specialties else "Есть"
        lines.append(f"{prefix} {unnamed} {_plural_doctors(unnamed)} "
                     "без подтверждённой специальности.")

    if len(answer.items) <= _MAX_NAMED:
        lines.append("; ".join(_named(item) for item in answer.items) + ".")
    else:
        lines.append(f"Всего {len(answer.items)} {_plural_doctors(len(answer.items))}.")
    return "\n".join(lines)


def format_nearest_quote(quote: str, cite: str) -> str:
    """Замена перечислению «Найдено N совпадений».

    Несколько похожих фрагментов — это не ответ, а результат поиска, и
    выдавать их списком контракт запрещает прямо. Но и промолчать о
    найденном неправильно: ближайший фрагмент честно назван ближайшим, а
    не ответом, и он один.
    """
    return f"Не нашёл прямого ответа. Ближайшее из ваших записей:\n{quote}\n\nИсточник: {cite}"
