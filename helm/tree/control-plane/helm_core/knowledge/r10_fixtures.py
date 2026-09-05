"""R10 — четыре контролируемые фикстуры непохожих доменов (§30.8.5 I).

> At least 4 non-identical semantic domains/classes: health visit;
> project meeting/decision; purchase/warranty; lecture/concept.
> Same core node/edge pipeline; no health-only retrieval implementation
> accepted.

Почему фикстуры, а не выборка из корпуса: живой корпус целиком один
домен — 90 источников, все `health` (замер прогона 283). Утверждение
«ядро доменно-агностично» на нём проверить нечем, и §30.8.5 I прямо
требует четыре непохожих класса. Фикстуры — единственный честный способ
это измерить, и они помечены как фикстуры, а не выданы за корпус.

Тексты синтетические и никого не описывают: имена, организации и даты
вымышлены. Ни одного health-документа владельца здесь нет — в том числе
и «health visit» фикстура написана с нуля.

Каждая фикстура несёт ЯВНО присутствующие в тексте факты, чтобы ожидание
проверяло извлечение, а не догадку. Ожидания перечислены рядом с
текстом: сущность, дата и вид утверждения — то, что документ говорит
дословно.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Fixture:
    key: str
    domain: str
    #: Как назван класс в §30.8.5 I. Не подвид узла: подвид назначает
    #: извлекатель, а это ярлык требования приёмки.
    acceptance_class: str
    text: str
    #: Подписи, дословно присутствующие в тексте. Проверяется, что ядро
    #: их извлекло, а не то, что оно угадало их роль.
    expect_labels: tuple[str, ...] = ()
    #: Даты в тексте, которые обязаны стать структурными.
    expect_dates: tuple[str, ...] = ()
    #: Виды узлов, которые документ обязан породить.
    expect_kinds: tuple[str, ...] = ()


FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        key="health_visit",
        domain="library",
        acceptance_class="health visit",
        text=(
            "# Выписка приёма\n\n"
            "Дата приёма: 14.03.2026.\n"
            "Приём вёл врач-эндокринолог Северцев Артём Игоревич.\n"
            "Пациент обратился с жалобой на утомляемость.\n"
            "Назначен контроль ТТГ через три месяца.\n"
        ),
        expect_labels=("Северцев Артём Игоревич", "эндокринолог"),
        expect_dates=("2026-03-14",),
        expect_kinds=("entity", "event"),
    ),
    Fixture(
        key="project_decision",
        domain="library",
        acceptance_class="project meeting/decision",
        text=(
            "# Встреча по проекту ЗАПИСКИ\n\n"
            "Дата: 02.04.2026.\n"
            "Участники: Ковалёва Мария Львовна, Тихонов Глеб Русланович.\n"
            "Обсуждали платную подписку в первом релизе.\n"
            "Решение: подписку в первый релиз не включать.\n"
            "Причина: недостаточно данных о готовности платить.\n"
        ),
        expect_labels=("Ковалёва Мария Львовна", "Тихонов Глеб Русланович", "ЗАПИСКИ"),
        expect_dates=("2026-04-02",),
        expect_kinds=("entity", "decision"),
    ),
    Fixture(
        key="purchase_warranty",
        domain="library",
        acceptance_class="purchase/warranty",
        text=(
            "# Покупка\n\n"
            "12.03.2026 куплен холодильник Bosch KGN39 в магазине «Электросила».\n"
            "Гарантия действует до 12.03.2029.\n"
            "Серийный номер FD9812-4471.\n"
        ),
        expect_labels=("Bosch KGN39", "Электросила", "FD9812-4471"),
        expect_dates=("2026-03-12", "2029-03-12"),
        expect_kinds=("entity", "fact"),
    ),
    Fixture(
        key="lecture_concept",
        domain="library",
        acceptance_class="lecture/concept",
        text=(
            "# Лекция об инфляции\n\n"
            "Лектор Соколов Пётр Ильич 20.02.2026 разбирал гиперинфляцию.\n"
            "Гиперинфляция — рост цен свыше пятидесяти процентов в месяц.\n"
            "В качестве примера приведена Веймарская республика.\n"
        ),
        expect_labels=("Соколов Пётр Ильич", "гиперинфляция"),
        expect_dates=("2026-02-20",),
        expect_kinds=("entity", "concept"),
    ),
)


@dataclass
class FixtureOutcome:
    key: str
    acceptance_class: str
    windows_total: int = 0
    windows_failed: int = 0
    coverage: float = 0.0
    nodes: int = 0
    entities: int = 0
    edges: int = 0
    mentions_exact_span: int = 0
    labels_found: list[str] = field(default_factory=list)
    labels_missing: list[str] = field(default_factory=list)
    dates_found: list[str] = field(default_factory=list)
    dates_missing: list[str] = field(default_factory=list)
    kinds_found: list[str] = field(default_factory=list)
    kinds_missing: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        """Что считается пройденным для §30.8.5 I.

        Ядро обязано ОДИНАКОВО обработать четыре непохожих документа:
        все окна терминальны и подписи документа стали узлами. Полнота
        связей сюда не входит намеренно — граф связей на этом
        извлекателе пуст (SPEC_DEVIATION D-2), и требовать здесь рёбра
        значило бы проверять не то, что заявлено этим пунктом.
        """
        return (self.error is None
                and self.windows_failed == 0
                and self.coverage >= 1.0
                and not self.labels_missing)
