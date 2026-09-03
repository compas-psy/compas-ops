"""R4 (§14.18) — golden benchmark: фиксированные fixtures с заранее заданным
ожидаемым результатом для замера локального semantic extractor.

Требование владельца: «Expected gold не генерировать тестируемой моделью».
Ожидания здесь написаны вручную, до какого-либо запуска модели-кандидата, и
не меняются по результатам замера — иначе бенчмарк проверял бы модель против
самой себя.

`canonical_text` у атома — не строка для точного сравнения (модель не обязана
цитировать источник дословно), а опорный текст: `semantic_benchmark.py` меряет
похожесть через неё, а НЕ смотрит, что «текст красивый». SYSTEM_PROMPT прямо
требует «пиши только то, что сказано в тексте буквально», поэтому у корректно
ведущей себя модели `atom.text` неизбежно близок к `canonical_text` — если
непохож, это либо перефраз с потерей сути, либо посторонний домысел.

`ref` — идентификатор ВНУТРИ фикстуры (для описания рёбер), не связан с
`local_id`, который придумывает сама модель на каждом прогоне.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GoldEntity:
    ref: str
    entity_type: str
    label: str
    subtype: str | None = None
    aliases: tuple[str, ...] = ()

    @property
    def critical(self) -> bool:
        """§14.18 hard gate «critical expected entity/event recall» — не
        общий entity_recall. CONCEPT (глоссарное понятие вроде «уролог»
        или «инфляция») — фоновое справочное знание, не тот «critical
        entity», чей пропуск угрожает точности личного архива владельца.
        PERSON/ORGANIZATION/PLACE — реальные участники зафиксированных
        фактов, критичны всегда."""
        return self.entity_type != "CONCEPT"


@dataclass(frozen=True)
class GoldAtom:
    ref: str
    kind: str
    canonical_text: str
    subtype: str | None = None
    #: ISO-подобная строка (YYYY-MM-DD/YYYY-MM/YYYY) или None, если в тексте
    #: вообще нет даты, к которой относится атом (тогда date_precision тоже
    #: None — поле неприменимо, а не «модель обязана написать unknown»).
    occurred_at: str | None = None
    #: None = дата в тексте не упоминается вовсе (поле неприменимо).
    #: "unknown" = дата упомянута, но не разрешима до дня/месяца/года —
    #: модель ОБЯЗАНА поставить именно unknown, а не промолчать и не
    #: выдумать точную дату (§14.8).
    date_precision: str | None = None
    #: Атом описывает ОТСУТСТВИЕ факта («диагноз не подтверждён»). Модель,
    #: потерявшая отрицание при перефразе, меняет смысл на противоположный —
    #: это не стилевая потеря, а материальная галлюцинация (R4 п.7).
    negation_sensitive: bool = False

    @property
    def critical(self) -> bool:
        """§14.18 hard gate «critical expected entity/EVENT recall» — не
        общий atom_recall. EVENT по `kind` ИЛИ атом с конкретной датой
        (`occurred_at` задан) — оба описывают ЧТО-ТО, что произошло в
        определённый момент, что и есть «event» в духе гейта, независимо
        от формального kind (например, purchase_warranty хранит дату
        покупки в FACT-атоме, не EVENT). FACT/DECISION/CONCEPT без даты —
        фон; их полнота покрыта другими метриками (unsupported critical
        facts, relation precision), не этим конкретным гейтом."""
        return self.kind == "event" or self.occurred_at is not None


@dataclass(frozen=True)
class GoldEdge:
    from_ref: str
    relation_type: str
    to_ref: str
    role: str | None = None


@dataclass(frozen=True)
class ForbiddenEdge:
    """Провокационный кейс: ребро между этими двумя ref быть не должно —
    текст описывает сущности как НЕ связанные, и его выдумывание — не
    неточность, а тот самый «unsupported relation», который R4 п.7
    называет hard gate, а не минусом к баллу."""

    from_ref: str
    to_ref: str


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    categories: tuple[str, ...]
    domain: str
    text: str
    heading_path: tuple[str, ...] = ()
    entities: tuple[GoldEntity, ...] = ()
    atoms: tuple[GoldAtom, ...] = ()
    edges: tuple[GoldEdge, ...] = ()
    forbidden_edges: tuple[ForbiddenEdge, ...] = ()
    #: Единственно верный ответ — три пустых списка (§14.4.3: «если во
    #: фрагменте нечего извлекать, верни три пустых списка»).
    expect_no_knowledge: bool = False
    notes: str = ""


GOLDEN_CASES: tuple[GoldenCase, ...] = (
    GoldenCase(
        case_id="doctor_visit",
        categories=("person_doctor", "event", "date_day", "typed_relations"),
        domain="health",
        heading_path=("Приём",),
        text=(
            "19 августа 2026 года состоялся приём врача-уролога Кириченко "
            "Сергея Александровича. Жалоб на момент осмотра не предъявлено."
        ),
        entities=(
            GoldEntity(ref="e1", entity_type="PERSON",
                      label="Кириченко Сергей Александрович", subtype="doctor"),
        ),
        atoms=(
            GoldAtom(ref="a1", kind="event",
                    canonical_text=(
                        "19 августа 2026 года состоялся приём врача-уролога "
                        "Кириченко Сергея Александровича."
                    ),
                    occurred_at="2026-08-19", date_precision="day"),
        ),
        edges=(GoldEdge(from_ref="a1", relation_type="involves", to_ref="e1", role="doctor"),),
    ),
    GoldenCase(
        case_id="organization_fact",
        categories=("organization", "fact", "date_year", "typed_relations"),
        domain="work",
        text=(
            "Иванова Мария работает менеджером по продажам в ООО «Ромашка». "
            "Трудовой договор подписан в 2023 году."
        ),
        entities=(
            GoldEntity(ref="e1", entity_type="PERSON", label="Иванова Мария"),
            GoldEntity(ref="e2", entity_type="ORGANIZATION", label="ООО «Ромашка»"),
        ),
        atoms=(
            GoldAtom(ref="a1", kind="fact",
                    canonical_text="Иванова Мария работает менеджером по продажам в ООО «Ромашка»."),
            GoldAtom(ref="a2", kind="fact", canonical_text="Трудовой договор подписан в 2023 году.",
                    occurred_at="2023", date_precision="year"),
        ),
        edges=(
            GoldEdge(from_ref="a1", relation_type="involves", to_ref="e1", role="employee"),
            GoldEdge(from_ref="a1", relation_type="involves", to_ref="e2"),
        ),
    ),
    GoldenCase(
        case_id="place_event",
        categories=("place", "event", "date_day", "typed_relations"),
        domain="personal",
        text="Встреча с друзьями состоялась в кафе «Пушкинъ» 5 сентября 2026 года.",
        entities=(GoldEntity(ref="e1", entity_type="PLACE", label="кафе «Пушкинъ»"),),
        atoms=(
            GoldAtom(ref="a1", kind="event",
                    canonical_text="Встреча с друзьями состоялась в кафе «Пушкинъ» 5 сентября 2026 года.",
                    occurred_at="2026-09-05", date_precision="day"),
        ),
        edges=(GoldEdge(from_ref="a1", relation_type="located_at", to_ref="e1"),),
    ),
    GoldenCase(
        case_id="concept_medical_specialty",
        categories=("concept_medical_specialty", "aliases", "typed_relations"),
        domain="health",
        text=(
            "Уролог — врач, специализирующийся на заболеваниях мочеполовой "
            "системы. Также специальность называют урологией."
        ),
        entities=(
            GoldEntity(ref="e1", entity_type="CONCEPT", label="уролог",
                      subtype="medical_specialty", aliases=("урология",)),
        ),
        atoms=(
            GoldAtom(ref="a1", kind="concept",
                    canonical_text=(
                        "Уролог — врач, специализирующийся на заболеваниях "
                        "мочеполовой системы."
                    )),
        ),
        edges=(GoldEdge(from_ref="a1", relation_type="about", to_ref="e1"),),
    ),
    GoldenCase(
        case_id="fact_plain",
        categories=("fact",),
        domain="personal",
        text="Рост курса доллара к концу года превысил ожидания аналитиков.",
        atoms=(
            GoldAtom(ref="a1", kind="fact",
                    canonical_text="Рост курса доллара к концу года превысил ожидания аналитиков."),
        ),
        notes="Атом без единой сущности — FACT в чистом виде, ничего связывать не с чем.",
    ),
    GoldenCase(
        case_id="event_month",
        categories=("event", "date_month"),
        domain="work",
        text="В июле 2026 года прошла ежегодная конференция разработчиков.",
        atoms=(
            GoldAtom(ref="a1", kind="event",
                    canonical_text="В июле 2026 года прошла ежегодная конференция разработчиков.",
                    occurred_at="2026-07", date_precision="month"),
        ),
    ),
    GoldenCase(
        case_id="decision_rationale",
        categories=("decision_rationale", "typed_relations", "meeting_project_decision"),
        domain="work",
        text=(
            "Тестирование проекта не завершено. Из-за этого было решено "
            "перенести запуск проекта на октябрь."
        ),
        atoms=(
            GoldAtom(ref="a1", kind="fact", canonical_text="Тестирование проекта не завершено.",
                    negation_sensitive=True),
            GoldAtom(ref="a2", kind="decision",
                    canonical_text="Было решено перенести запуск проекта на октябрь."),
        ),
        edges=(GoldEdge(from_ref="a1", relation_type="reason_for", to_ref="a2"),),
    ),
    GoldenCase(
        case_id="aliases",
        categories=("aliases", "organization", "typed_relations"),
        domain="purchases",
        text="Оплата прошла через Сбербанк (СБЕР). Полное наименование — ПАО Сбербанк.",
        entities=(
            GoldEntity(ref="e1", entity_type="ORGANIZATION", label="Сбербанк",
                      aliases=("СБЕР", "ПАО Сбербанк")),
        ),
        atoms=(GoldAtom(ref="a1", kind="fact", canonical_text="Оплата прошла через Сбербанк."),),
        edges=(GoldEdge(from_ref="a1", relation_type="involves", to_ref="e1"),),
    ),
    GoldenCase(
        case_id="multi_entity_atom",
        categories=("multi_entity_atom", "typed_relations"),
        domain="work",
        text=(
            "На совещании присутствовали Смирнов Олег и Петрова Анна. "
            "Смирнов Олег представил отчёт по продажам. "
            "Петрова Анна предложила увеличить бюджет на маркетинг."
        ),
        entities=(
            GoldEntity(ref="e1", entity_type="PERSON", label="Смирнов Олег"),
            GoldEntity(ref="e2", entity_type="PERSON", label="Петрова Анна"),
        ),
        atoms=(
            GoldAtom(ref="a1", kind="event",
                    canonical_text="На совещании присутствовали Смирнов Олег и Петрова Анна."),
            GoldAtom(ref="a2", kind="fact",
                    canonical_text="Смирнов Олег представил отчёт по продажам."),
            GoldAtom(ref="a3", kind="decision",
                    canonical_text="Петрова Анна предложила увеличить бюджет на маркетинг."),
        ),
        edges=(
            GoldEdge(from_ref="a1", relation_type="involves", to_ref="e1"),
            GoldEdge(from_ref="a1", relation_type="involves", to_ref="e2"),
            GoldEdge(from_ref="a2", relation_type="involves", to_ref="e1"),
            GoldEdge(from_ref="a3", relation_type="involves", to_ref="e2"),
        ),
    ),
    GoldenCase(
        case_id="typed_relations_variety",
        categories=("typed_relations",),
        domain="work",
        text=(
            "Смирнов Олег отвечает за раздел «Регионы». Из-за высокой "
            "нагрузки Смирнов Олег попросил помощника. В результате скорость "
            "подготовки отчётов выросла. Данные Петровой Анны подтверждают "
            "этот вывод."
        ),
        entities=(
            GoldEntity(ref="e1", entity_type="PERSON", label="Смирнов Олег"),
            GoldEntity(ref="e2", entity_type="PERSON", label="Петрова Анна"),
        ),
        atoms=(
            GoldAtom(ref="a1", kind="fact", canonical_text="Смирнов Олег отвечает за раздел «Регионы»."),
            GoldAtom(ref="a2", kind="fact",
                    canonical_text="Из-за высокой нагрузки Смирнов Олег попросил помощника."),
            GoldAtom(ref="a3", kind="fact", canonical_text="Скорость подготовки отчётов выросла."),
            GoldAtom(ref="a4", kind="fact",
                    canonical_text="Данные Петровой Анны подтверждают этот вывод."),
        ),
        edges=(
            GoldEdge(from_ref="a1", relation_type="involves", to_ref="e1"),
            GoldEdge(from_ref="a2", relation_type="resulted_in", to_ref="a3"),
            GoldEdge(from_ref="a4", relation_type="supports", to_ref="a3"),
            GoldEdge(from_ref="a4", relation_type="involves", to_ref="e2"),
        ),
        notes="Разные типы связей в одном окне: involves/resulted_in/supports.",
    ),
    GoldenCase(
        case_id="date_year",
        categories=("date_year", "place", "event"),
        domain="work",
        text="В 2023 году компания открыла новый филиал в Казани.",
        entities=(GoldEntity(ref="e1", entity_type="PLACE", label="Казань"),),
        atoms=(
            GoldAtom(ref="a1", kind="event",
                    canonical_text="В 2023 году компания открыла новый филиал в Казани.",
                    occurred_at="2023", date_precision="year"),
        ),
        edges=(GoldEdge(from_ref="a1", relation_type="located_at", to_ref="e1"),),
    ),
    GoldenCase(
        case_id="date_unknown",
        categories=("date_unknown", "provocative_no_date"),
        domain="work",
        text="В прошлый вторник встречались по поводу нового контракта, но детали пока не согласованы.",
        atoms=(
            GoldAtom(ref="a1", kind="event",
                    canonical_text="В прошлый вторник встречались по поводу нового контракта.",
                    occurred_at=None, date_precision="unknown"),
        ),
        notes=(
            "Относительная дата без опоры — occurred_at должен остаться "
            "пустым с date_precision=unknown; выдуманная точная дата — "
            "hard gate (R4 п.7 «fabricated precise date»)."
        ),
    ),
    GoldenCase(
        case_id="same_label_different_entities",
        categories=("same_label_different_entities", "typed_relations"),
        domain="health",
        text=(
            "Приём вёл терапевт Иванов. Позже документы по этому визиту "
            "подписал юрист Иванов из страховой компании."
        ),
        entities=(
            GoldEntity(ref="e1", entity_type="PERSON", label="Иванов", subtype="doctor"),
            GoldEntity(ref="e2", entity_type="PERSON", label="Иванов", subtype="lawyer"),
        ),
        atoms=(
            GoldAtom(ref="a1", kind="event", canonical_text="Приём вёл терапевт Иванов."),
            GoldAtom(ref="a2", kind="fact",
                    canonical_text="Документы по этому визиту подписал юрист Иванов из страховой компании."),
        ),
        edges=(
            GoldEdge(from_ref="a1", relation_type="involves", to_ref="e1", role="doctor"),
            GoldEdge(from_ref="a2", relation_type="involves", to_ref="e2", role="lawyer"),
        ),
        notes=(
            "Один и тот же label «Иванов» в ОДНОМ окне — два разных "
            "человека по контексту (§14.7 разрешение сущностей это R6, "
            "но склейка ДВУХ local_id в один внутри одного ответа — уже "
            "дефект самого извлечения, не последующего resolution)."
        ),
    ),
    GoldenCase(
        case_id="negative_statement",
        categories=("negative_statement", "provocative_no_fact_invention"),
        domain="health",
        text=(
            "Онкологический диагноз не подтверждён по результатам биопсии. "
            "Дальнейшее наблюдение не требуется."
        ),
        atoms=(
            GoldAtom(ref="a1", kind="fact",
                    canonical_text="Онкологический диагноз не подтверждён по результатам биопсии.",
                    negation_sensitive=True),
            GoldAtom(ref="a2", kind="fact", canonical_text="Дальнейшее наблюдение не требуется.",
                    negation_sensitive=True),
        ),
        notes=(
            "Потеря отрицания при извлечении («диагностирована онкология») "
            "меняет факт на противоположный — material hallucination, а не "
            "стилевая неточность."
        ),
    ),
    GoldenCase(
        case_id="ambiguous_text",
        categories=("ambiguous_text", "provocative_no_fact_invention"),
        domain="personal",
        text="Кто-то из коллег упомянул перенос встречи, но не уточнил, кто именно и на какую дату.",
        atoms=(
            GoldAtom(ref="a1", kind="event",
                    canonical_text="Кто-то из коллег упомянул перенос встречи.",
                    occurred_at=None, date_precision="unknown"),
        ),
        notes=(
            "В тексте нет имени — сущность придумывать нельзя. Ноль entities "
            "и есть верный ответ, а не пропуск."
        ),
    ),
    GoldenCase(
        case_id="no_knowledge",
        categories=("no_knowledge",),
        domain="personal",
        text="Документ сформирован автоматически. Версия шаблона: 1.4. Не редактировать вручную.",
        expect_no_knowledge=True,
        notes=(
            "Владелец 03.09.2026: NO_KNOWLEDGE = нет долговременного "
            "пользовательского знания, а не «показалось неинтересным». "
            "Прежний текст («ничего примечательного не произошло») сам был "
            "утверждением о дне владельца — модель, вернувшая по нему атом, "
            "была не обязательно неправа, и все три кандидата в R4.5.1 "
            "провалили именно этот кейс одинаково. Boilerplate/formatting-"
            "фрагмент документа (штамп версии шаблона) однозначен: в нём "
            "нет ни одного утверждения о жизни владельца — извлекать нечего "
            "по определению, а не по вкусу модели."
        ),
    ),
    GoldenCase(
        case_id="long_dense_window",
        categories=(
            "long_dense_window", "multi_entity_atom", "typed_relations", "date_day",
            "date_month", "decision_rationale", "organization", "place",
            "meeting_project_decision",
        ),
        domain="work",
        heading_path=("Протокол совещания по проекту «Горизонт»",),
        text=(
            "20 января 2026 года состоялось совещание с участием Смирнова "
            "Олега (руководитель проекта), Петровой Анны (аналитик) и "
            "представителя заказчика — компании ООО «СтройИнвест». Встреча "
            "прошла в переговорной комнате офиса на Тверской улице.\n\n"
            "Смирнов Олег представил отчёт о ходе строительства. Петрова "
            "Анна отметила отставание от графика на две недели из-за "
            "задержки поставки материалов поставщиком ООО «МеталлТорг». "
            "Иначе заказчик грозил расторжением договора, поэтому было "
            "решено перенести срок сдачи объекта на март 2026 года. Также "
            "решено сменить поставщика материалов на ООО «БазисСнаб».\n\n"
            "Следующее совещание назначено на 3 февраля 2026 года."
        ),
        entities=(
            GoldEntity(ref="e1", entity_type="PERSON", label="Смирнов Олег",
                      subtype="project_manager"),
            GoldEntity(ref="e2", entity_type="PERSON", label="Петрова Анна", subtype="analyst"),
            GoldEntity(ref="e3", entity_type="ORGANIZATION", label="ООО «СтройИнвест»"),
            GoldEntity(ref="e4", entity_type="PLACE",
                      label="переговорная комната офиса на Тверской улице"),
            GoldEntity(ref="e5", entity_type="ORGANIZATION", label="ООО «МеталлТорг»"),
            GoldEntity(ref="e6", entity_type="ORGANIZATION", label="ООО «БазисСнаб»"),
        ),
        atoms=(
            GoldAtom(ref="a1", kind="event",
                    canonical_text=(
                        "20 января 2026 года состоялось совещание с участием "
                        "Смирнова Олега, Петровой Анны и представителя "
                        "заказчика — компании ООО «СтройИнвест»."
                    ),
                    occurred_at="2026-01-20", date_precision="day"),
            GoldAtom(ref="a2", kind="fact",
                    canonical_text="Смирнов Олег представил отчёт о ходе строительства."),
            GoldAtom(ref="a3", kind="fact",
                    canonical_text=(
                        "Петрова Анна отметила отставание от графика на две "
                        "недели из-за задержки поставки материалов "
                        "поставщиком ООО «МеталлТорг»."
                    )),
            GoldAtom(ref="a4", kind="fact",
                    canonical_text="Заказчик грозил расторжением договора."),
            GoldAtom(ref="a5", kind="decision",
                    canonical_text="Было решено перенести срок сдачи объекта на март 2026 года.",
                    occurred_at="2026-03", date_precision="month"),
            GoldAtom(ref="a6", kind="decision",
                    canonical_text="Решено сменить поставщика материалов на ООО «БазисСнаб»."),
            GoldAtom(ref="a7", kind="event",
                    canonical_text="Следующее совещание назначено на 3 февраля 2026 года.",
                    occurred_at="2026-02-03", date_precision="day"),
        ),
        edges=(
            GoldEdge(from_ref="a1", relation_type="located_at", to_ref="e4"),
            GoldEdge(from_ref="a1", relation_type="involves", to_ref="e1", role="project_manager"),
            GoldEdge(from_ref="a1", relation_type="involves", to_ref="e2", role="analyst"),
            GoldEdge(from_ref="a1", relation_type="involves", to_ref="e3"),
            GoldEdge(from_ref="a2", relation_type="involves", to_ref="e1"),
            GoldEdge(from_ref="a3", relation_type="involves", to_ref="e2"),
            GoldEdge(from_ref="a3", relation_type="about", to_ref="e5"),
            GoldEdge(from_ref="a4", relation_type="reason_for", to_ref="a5"),
            GoldEdge(from_ref="a6", relation_type="involves", to_ref="e6"),
        ),
    ),
    GoldenCase(
        case_id="provocative_no_relation_invention",
        categories=("provocative_no_relation", "negative_statement"),
        domain="work",
        text=(
            "В отчёте упомянуты два сотрудника: Кузнецов Игорь работает в "
            "отделе продаж, а Волкова Елена — в отделе кадров. Они не "
            "участвуют в одних и тех же проектах."
        ),
        entities=(
            GoldEntity(ref="e1", entity_type="PERSON", label="Кузнецов Игорь"),
            GoldEntity(ref="e2", entity_type="PERSON", label="Волкова Елена"),
        ),
        atoms=(
            GoldAtom(ref="a1", kind="fact", canonical_text="Кузнецов Игорь работает в отделе продаж."),
            GoldAtom(ref="a2", kind="fact", canonical_text="Волкова Елена работает в отделе кадров."),
            GoldAtom(ref="a3", kind="fact",
                    canonical_text="Они не участвуют в одних и тех же проектах.",
                    negation_sensitive=True),
        ),
        edges=(
            GoldEdge(from_ref="a1", relation_type="involves", to_ref="e1"),
            GoldEdge(from_ref="a2", relation_type="involves", to_ref="e2"),
            GoldEdge(from_ref="a3", relation_type="involves", to_ref="e1"),
            GoldEdge(from_ref="a3", relation_type="involves", to_ref="e2"),
        ),
        forbidden_edges=(
            ForbiddenEdge(from_ref="e1", to_ref="e2"),
            ForbiddenEdge(from_ref="e2", to_ref="e1"),
        ),
        notes=(
            "Текст явно отрицает связь между e1 и e2 — любое ребро "
            "напрямую между ними является выдуманной связью (R4 п.7 "
            "«fabricated relations», hard gate)."
        ),
    ),
    GoldenCase(
        case_id="provocative_no_fact_invention",
        categories=("provocative_no_fact",),
        domain="personal",
        text="В списке участников значится Соколов Артём. Подробности его роли в документе не указаны.",
        entities=(GoldEntity(ref="e1", entity_type="PERSON", label="Соколов Артём"),),
        atoms=(
            GoldAtom(ref="a1", kind="fact",
                    canonical_text="В списке участников значится Соколов Артём."),
        ),
        edges=(GoldEdge(from_ref="a1", relation_type="involves", to_ref="e1"),),
        notes=(
            "Владелец 03.09.2026: «значится в списке участников» — тоже "
            "факт из текста, его можно и нужно извлечь; запрещена именно "
            "ВЫДУМАННАЯ роль или действие сверх этого (например, «отвечал "
            "за логистику») — текст прямо говорит, что подробностей роли в "
            "документе нет. Прежний gold (0 атомов вообще) противоречил "
            "собственному тексту фикстуры: 2 из 3 моделей в R4.5.1 "
            "восстановили именно этот факт и были за это оштрафованы."
        ),
    ),
    GoldenCase(
        case_id="purchase_warranty",
        categories=("purchase_warranty", "organization", "date_day", "fact"),
        domain="purchases",
        text=(
            "Ноутбук ASUS куплен 10 февраля 2026 года в магазине «Ситилинк». "
            "Гарантийный срок — 12 месяцев. При поломке в течение гарантии "
            "ремонт бесплатный."
        ),
        entities=(GoldEntity(ref="e1", entity_type="ORGANIZATION", label="«Ситилинк»"),),
        atoms=(
            GoldAtom(ref="a1", kind="fact",
                    canonical_text="Ноутбук ASUS куплен 10 февраля 2026 года в магазине «Ситилинк».",
                    occurred_at="2026-02-10", date_precision="day"),
            GoldAtom(ref="a2", kind="fact", canonical_text="Гарантийный срок — 12 месяцев."),
            GoldAtom(ref="a3", kind="fact",
                    canonical_text="При поломке в течение гарантии ремонт бесплатный."),
        ),
        edges=(GoldEdge(from_ref="a1", relation_type="located_at", to_ref="e1"),),
    ),
    GoldenCase(
        case_id="lecture_concept",
        categories=("lecture_concept", "concept_medical_specialty"),
        domain="learning",
        notes="Не про медицину — второй CONCEPT-кейс, чтобы medical_specialty не был единственным.",
        text=(
            "На лекции по экономике рассматривалось понятие инфляции — "
            "устойчивого роста общего уровня цен. Лектор Соколов привёл "
            "пример гиперинфляции как крайней формы этого явления."
        ),
        entities=(
            GoldEntity(ref="e1", entity_type="CONCEPT", label="инфляция", subtype="economic_concept"),
            GoldEntity(ref="e2", entity_type="PERSON", label="Соколов", subtype="lecturer"),
            GoldEntity(ref="e3", entity_type="CONCEPT", label="гиперинфляция", subtype="economic_concept"),
        ),
        atoms=(
            GoldAtom(ref="a1", kind="concept",
                    canonical_text="Инфляция — устойчивый рост общего уровня цен."),
            GoldAtom(ref="a2", kind="fact",
                    canonical_text=(
                        "Лектор Соколов привёл пример гиперинфляции как "
                        "крайней формы этого явления."
                    )),
        ),
        edges=(
            GoldEdge(from_ref="a1", relation_type="about", to_ref="e1"),
            GoldEdge(from_ref="a2", relation_type="involves", to_ref="e2"),
            GoldEdge(from_ref="a2", relation_type="about", to_ref="e3"),
            GoldEdge(from_ref="e3", relation_type="related_to", to_ref="e1"),
        ),
    ),
)


#: Категории, которые владелец назвал обязательными к покрытию (R4 п.2).
#: Свойство теста в semantic_benchmark_fixtures_test — «каждая здесь встречена
#: хотя бы одной фикстурой» — ловит пробел в покрытии раньше живого прогона.
REQUIRED_CATEGORIES = frozenset({
    "person_doctor", "organization", "place", "concept_medical_specialty",
    "fact", "event", "decision_rationale", "aliases", "typed_relations",
    "date_day", "date_month", "date_year", "date_unknown", "multi_entity_atom",
    "same_label_different_entities", "negative_statement", "ambiguous_text",
    "no_knowledge", "long_dense_window", "provocative_no_date",
    "provocative_no_relation", "provocative_no_fact", "purchase_warranty",
    "lecture_concept", "meeting_project_decision",
})
