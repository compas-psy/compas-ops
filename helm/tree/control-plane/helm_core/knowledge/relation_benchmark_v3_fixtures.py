"""R4.6.F1.2 (владелец 03.09.2026) — ЗАМОРОЖЕННЫЙ benchmark v3 relation
NLI: новые, отдельные от `semantic_benchmark_fixtures.GOLDEN_CASES`
fixtures (владелец п.1: старые GOLDEN_CASES не трогать, историческая
сравнимость R4 не должна ломаться).

Контракт (владелец п.6-9):

- Каждый кейс явно объявляет ОБА списка — `entailed` (позитивы) и
  `not_entailed` (явные hard negatives с человекочитаемой `reason`).
  «Отсутствует в `entailed`» НИКОГДА не читается как «ложно» — это была
  методологическая дыра v1 (false_pair на эвристике «нет в gold»),
  здесь закрыта тем, что единственный источник негатива — explicit
  `not_entailed`, у каждого — причина, почему это неверно, написанная
  вручную ДО какого-либо прогона модели-кандидата.
- Одна пара узлов может одновременно нести НЕСКОЛЬКО типов связи
  (например, ATOM `about` понятия И `involves` человека) — это не
  конфликт, contract §14.9/`docs/R4.6.F1.2-RELATION-ONTOLOGY.md`.
- Все hypothesis строятся `RelationVerbalizerV3` (quoted reference) —
  ни одна negative-пара здесь не является структурно неверблизуемой
  (`UNSUPPORTED_FOR_NLI`) — иначе для неё нет NLI-примера, который можно
  было бы измерить.
- Покрытие (владелец п.7): ≥6 positive и ≥3 отдельных `case_id` на
  каждый из 15 `SemanticRelationType`; ≥2 явных hard negative на каждый
  positive (в среднем по кейсу, не жёстко 1:1 — см. coverage-тест).
  `HAS_ROLE` — обязательное отдельное покрытие (§14.9, R7): кейсы
  `clinic_visit_specialty`/`project_meeting_full` намеренно ставят
  `HAS_ROLE` (человек → понятие-специальность, атомонезависимо) РЯДОМ
  с `INVOLVES(role=...)` (человек — сторона ОДНОГО конкретного атома)
  на одном и том же человеке, чтобы прямо противопоставить эти два
  разных факта, а не смешать их.
- Fixtures — ручная работа автора этого файла (не тестируемой модели):
  ни `premise`, ни `entailed`/`not_entailed` не сгенерированы
  mDeBERTa/rubert и не сверялись с их выводом до заморозки.

Заморозка (владелец п.9): после коммита этого файла `split` каждого
`case_id` НЕ меняется. `final_holdout` — 4 кейса (17 positives),
покрывающие все 15 типов минимум по разу, использованные ТОЛЬКО для
финального отчёта, никогда для подбора порога. `calibration` — 16
кейсов (78 positives) — единственный источник LOOCV/threshold-подбора.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Split = Literal["calibration", "final_holdout"]


@dataclass(frozen=True)
class RelEntity:
    ref: str
    entity_type: str
    label: str
    subtype: str | None = None


@dataclass(frozen=True)
class RelAtom:
    ref: str
    kind: str
    canonical_text: str


@dataclass(frozen=True)
class RelPositive:
    from_ref: str
    relation_type: str
    to_ref: str
    role: str | None = None


@dataclass(frozen=True)
class RelNegative:
    from_ref: str
    relation_type: str
    to_ref: str
    reason: str


@dataclass(frozen=True)
class RelationCaseV3:
    case_id: str
    split: Split
    domain: str
    text: str
    entities: tuple[RelEntity, ...] = ()
    atoms: tuple[RelAtom, ...] = ()
    entailed: tuple[RelPositive, ...] = ()
    not_entailed: tuple[RelNegative, ...] = ()
    notes: str = ""


RELATION_BENCHMARK_V3_CASES: tuple[RelationCaseV3, ...] = (
    RelationCaseV3(
        case_id="v3_clinic_visit_specialty",
        split="calibration",
        domain="health",
        text=(
            "15 марта 2026 года в клинике «Здоровье+» состоялся приём врача-нефролога "
            "Гавриловой Марины Сергеевны. Приём был посвящён теме хронической почечной "
            "недостаточности. Гаврилова Марина Сергеевна работает нефрологом уже двенадцать "
            "лет. В той же клинике по вторникам ведёт приём врач-кардиолог Орлов Дмитрий."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Гаврилова Марина Сергеевна"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="клиника «Здоровье+»"),
            RelEntity(ref="e3", entity_type="CONCEPT", label="нефролог", subtype="medical_specialty"),
            RelEntity(ref="e4", entity_type="CONCEPT", label="хроническая почечная недостаточность"),
            RelEntity(ref="e5", entity_type="PERSON", label="Орлов Дмитрий"),
            RelEntity(ref="e6", entity_type="CONCEPT", label="кардиолог", subtype="medical_specialty"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="event",
                   canonical_text="15 марта 2026 года в клинике «Здоровье+» состоялся приём врача-нефролога Гавриловой Марины Сергеевны."),
            RelAtom(ref="a2", kind="fact", canonical_text="Приём был посвящён теме хронической почечной недостаточности."),
            RelAtom(ref="a3", kind="fact", canonical_text="В той же клинике по вторникам ведёт приём врач-кардиолог Орлов Дмитрий."),
        ),
        entailed=(
            RelPositive("a1", "involves", "e1", role="doctor"),
            RelPositive("a1", "located_at", "e2"),
            RelPositive("a2", "about", "e4"),
            RelPositive("e1", "has_role", "e3"),
            RelPositive("e5", "has_role", "e6"),
            RelPositive("a3", "located_at", "e2"),
        ),
        not_entailed=(
            RelNegative("a2", "about", "e3", "about относится к теме ХПН (e4), не к специальности нефролог (e3) — специальность фиксирует has_role, не about факта о содержании приёма."),
            RelNegative("e5", "has_role", "e4", "Орлов (e5) не связан текстом с темой ХПН (e4) как ролью — его специальность e6 (кардиолог)."),
            RelNegative("a1", "involves", "e5", "Орлов Дмитрий упомянут в отдельном предложении о другом дне приёма — не участник события a1."),
            RelNegative("a3", "involves", "e1", "a3 описывает приём Орлова по вторникам — Гаврилова в этом предложении не упомянута."),
            RelNegative("e1", "has_role", "e4", "e4 — название темы/болезни (ХПН), не профессиональная специальность Гавриловой; её специальность — e3 (нефролог)."),
            RelNegative("e1", "has_role", "e6", "Гаврилова — нефролог (e3), не кардиолог (e6) — специальность Орлова, не её."),
            RelNegative("e5", "has_role", "e3", "Орлов — кардиолог (e6), не нефролог (e3) — специальность Гавриловой, не его."),
            RelNegative("a3", "about", "e6", "a3 называет специальность Орлова через прямое упоминание в тексте («врач-кардиолог») — это структурная роль (has_role), не тематическая привязка факта (about)."),
            RelNegative("a2", "about", "e6", "a2 посвящена ХПН (e4), не кардиологии (e6) — тема Орлова текстом не связывается с этим фактом."),
            RelNegative("a2", "involves", "e5", "a2 — факт о теме приёма Гавриловой, Орлов в нём не упомянут."),
            RelNegative("a1", "about", "e4", "about в этом кейсе размечена для a2 (отдельного факта о теме приёма), не для a1 (события самого приёма)."),
            RelNegative("a1", "about", "e3", "about в этом кейсе размечена для темы ХПН (a2→e4); a1 описывает сам факт визита, а не 'тему' специальности нефролог."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_clinic_report_authorship",
        split="calibration",
        domain="health",
        text=(
            "Заключение по итогам обследования Ковалёва Артёма составлено на основе "
            "результатов анализов и подготовлено врачом-нефрологом Крыловой Анной Ивановной. "
            "Результаты анализов сделаны неделей ранее. Консультация от 2 марта 2026 года "
            "зафиксирована в отдельном протоколе. В тексте заключения есть ссылка на протокол "
            "консультации от 2 марта 2026 года. Заключение посвящено теме хронической болезни почек."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Крылова Анна Ивановна"),
            RelEntity(ref="e2", entity_type="CONCEPT", label="хроническая болезнь почек"),
            RelEntity(ref="e3", entity_type="PERSON", label="Ковалёв Артём"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact",
                   canonical_text="Заключение по итогам обследования Ковалёва Артёма составлено на основе результатов анализов и подготовлено врачом-нефрологом Крыловой Анной Ивановной."),
            RelAtom(ref="a2", kind="fact", canonical_text="Результаты анализов сделаны неделей ранее."),
            RelAtom(ref="a3", kind="fact", canonical_text="Консультация от 2 марта 2026 года зафиксирована в отдельном протоколе."),
            RelAtom(ref="a4", kind="fact", canonical_text="В тексте заключения есть ссылка на протокол консультации от 2 марта 2026 года."),
            RelAtom(ref="a5", kind="fact", canonical_text="Заключение посвящено теме хронической болезни почек."),
        ),
        entailed=(
            RelPositive("a1", "created_by", "e1"),
            RelPositive("a1", "derived_from", "a2"),
            RelPositive("a4", "refers_to", "a3"),
            RelPositive("a5", "about", "e2"),
        ),
        not_entailed=(
            RelNegative("a2", "created_by", "e1", "Крылова готовила заключение (a1), а не сами результаты анализов (a2) — a2 создан лабораторией, текстом не названной."),
            RelNegative("a1", "created_by", "e3", "заключение подготовлено Крыловой (e1), не пациентом Ковалёвым (e3)."),
            RelNegative("a2", "derived_from", "a3", "результаты анализов (a2) не основаны на протоколе консультации (a3) — независимые источники, производности текст не утверждает."),
            RelNegative("a3", "refers_to", "a4", "reversed_direction: ссылку на протокол (a3) делает заключение (a4), не наоборот."),
            RelNegative("a1", "refers_to", "a3", "ссылку на протокол делает отдельное предложение a4, не сам факт об авторстве/основе a1."),
            RelNegative("a1", "about", "e2", "about в этом кейсе относится к a5 (отдельно сформулированной теме), не к a1 (факту об авторстве и основе)."),
            RelNegative("a5", "created_by", "e1", "a5 — про тему заключения, авторство утверждает a1, не a5 (разные факты)."),
            RelNegative("a4", "about", "e2", "a4 — факт о наличии ссылки на протокол, тему хронической болезни почек не упоминает; about размечена только для a5."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_clinic_diagnosis_conflict",
        split="calibration",
        domain="health",
        text=(
            "У пациента были жалобы, типичные для гастрита, и терапевт поставил диагноз "
            "«гастрит» на основании этих жалоб. По результатам гастроскопии врач-"
            "гастроэнтеролог поставил новый диагноз — язвенная болезнь, который заменил "
            "собой диагноз терапевта. Результаты гастроскопии показали наличие язвы и "
            "подтверждают новый диагноз. Предположение о гастрите противоречит результатам "
            "гастроскопии."
        ),
        atoms=(
            RelAtom(ref="a1", kind="decision", canonical_text="Терапевт поставил диагноз «гастрит» на основании жалоб пациента."),
            RelAtom(ref="a2", kind="fact", canonical_text="У пациента были жалобы, типичные для гастрита."),
            RelAtom(ref="a3", kind="decision", canonical_text="Врач-гастроэнтеролог поставил диагноз «язвенная болезнь» по результатам гастроскопии."),
            RelAtom(ref="a4", kind="fact", canonical_text="Результаты гастроскопии показали наличие язвы."),
        ),
        entailed=(
            RelPositive("a2", "reason_for", "a1"),
            RelPositive("a4", "supports", "a3"),
            RelPositive("a3", "supersedes", "a1"),
            RelPositive("a1", "contradicts", "a4"),
        ),
        not_entailed=(
            RelNegative("a4", "reason_for", "a1", "результаты гастроскопии (a4) не названы текстом причиной ПЕРВОГО диагноза (a1) — они появились позже и стали основанием для НОВОГО диагноза a3, а не a1."),
            RelNegative("a2", "supports", "a1", "a2 (жалобы) — основание диагноза (reason_for), а не независимое свидетельство, подтверждающее его (supports) — эти два факта в одном предложении играют разные роли."),
            RelNegative("a1", "supersedes", "a3", "reversed_direction: новый диагноз a3 заменяет старый a1, не наоборот."),
            RelNegative("a2", "contradicts", "a4", "жалобы пациента (a2) не противоречат результатам гастроскопии (a4) — последовательные звенья одного диагностического процесса, не взаимоисключающие утверждения."),
            RelNegative("a2", "supersedes", "a1", "a2 — жалобы (обоснование), не отдельное решение/диагноз, способное заменить a1."),
            RelNegative("a1", "contradicts", "a3", "a1 и a3 — сменяющие друг друга диагнозы (supersedes), а не одновременно заявленные несовместимые утверждения (contradicts) — текст явно называет a3 заменой a1."),
            RelNegative("a4", "reason_for", "a3", "a4 — свидетельство, подтверждающее диагноз (supports), не обоснование РЕШЕНИЯ поставить диагноз, отличное от самого диагноза."),
            RelNegative("a2", "contradicts", "a3", "жалобы пациента (a2) не противоречат новому диагнозу (a3) — они согласуются с более ранним диагнозом a1; явное противоречие текст фиксирует только между a1 и a4."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_project_meeting_full",
        split="calibration",
        domain="work",
        text=(
            "12 мая 2026 года в переговорной комнате офиса на Ленинском проспекте состоялось "
            "совещание отдела внедрения с участием руководителя проекта Волошина Артёма и "
            "аналитика Дементьевой Ольги. Волошин Артём отвечает за роль руководителя проекта "
            "уже второй год. Отдел внедрения входит в состав ООО «ТехноСтрой». Тестировщиков "
            "не хватало для соблюдения графика, и было решено перенести дату сдачи модуля на "
            "июнь. Перенос даты потребовал уведомить заказчика дополнительным письмом."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Волошин Артём"),
            RelEntity(ref="e2", entity_type="PERSON", label="Дементьева Ольга"),
            RelEntity(ref="e3", entity_type="PLACE", label="переговорная комната офиса на Ленинском проспекте"),
            RelEntity(ref="e4", entity_type="ORGANIZATION", label="Отдел внедрения"),
            RelEntity(ref="e5", entity_type="ORGANIZATION", label="ООО «ТехноСтрой»"),
            RelEntity(ref="e6", entity_type="CONCEPT", label="руководитель проекта", subtype="role_concept"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="event",
                   canonical_text="12 мая 2026 года в переговорной комнате офиса на Ленинском проспекте состоялось совещание отдела внедрения с участием руководителя проекта Волошина Артёма и аналитика Дементьевой Ольги."),
            RelAtom(ref="a2", kind="fact", canonical_text="Волошин Артём отвечает за роль руководителя проекта уже второй год."),
            RelAtom(ref="a3", kind="fact", canonical_text="Тестировщиков не хватало для соблюдения графика."),
            RelAtom(ref="a4", kind="decision", canonical_text="Было решено перенести дату сдачи модуля на июнь."),
            RelAtom(ref="a5", kind="fact", canonical_text="Перенос даты потребовал уведомить заказчика дополнительным письмом."),
        ),
        entailed=(
            RelPositive("a1", "involves", "e1", role="project_manager"),
            RelPositive("a1", "involves", "e2", role="analyst"),
            RelPositive("a1", "located_at", "e3"),
            RelPositive("e1", "has_role", "e6"),
            RelPositive("e4", "part_of", "e5"),
            RelPositive("a3", "reason_for", "a4"),
            RelPositive("a4", "resulted_in", "a5"),
        ),
        not_entailed=(
            RelNegative("a1", "involves", "e5", "ООО «ТехноСтрой» упомянута только в связи с принадлежностью отдела (part_of); участие в самом совещании a1 текст не описывает."),
            RelNegative("e2", "has_role", "e6", "текст не называет Дементьеву руководителем проекта — эта роль зафиксирована только за Волошиным."),
            RelNegative("e5", "part_of", "e4", "reversed_direction: отдел — часть организации, не наоборот."),
            RelNegative("a5", "reason_for", "a4", "уведомление заказчика (a5) — следствие решения a4 (resulted_in), а не отдельная причина, обосновывающая это же решение задним числом."),
            RelNegative("a3", "resulted_in", "a5", "прямая цепь текста — a3 (причина) → a4 (решение) → a5 (следствие); a3 не назван текстом напрямую приведшим к a5, минуя a4."),
            RelNegative("a2", "involves", "e2", "a2 — факт о роли Волошина, Дементьева в нём не упомянута."),
            RelNegative("a1", "located_at", "e5", "a1 произошло в переговорной комнате (e3); ООО «ТехноСтрой» упомянута как владелец отдела, не как физическое место события."),
            RelNegative("a4", "involves", "e4", "a4 — решение о переносе даты; отдел явно не назван его 'участником' — involves говорит про сторону события/факта, не про то, кого решение касается."),
            RelNegative("a2", "reason_for", "a4", "a2 описывает многолетний факт о роли Волошина — причина решения о переносе даты — нехватка тестировщиков (a3), не a2."),
            RelNegative("a2", "located_at", "e3", "a2 — факт о роли Волошина, места не касается; located_at размечен только для a1 (событие совещания)."),
            RelNegative("a1", "about", "e6", "about не размечена для роли 'руководитель проекта' — эта роль зафиксирована как has_role (e1→e6), не тема события a1."),
            RelNegative("a3", "involves", "e2", "a3 — факт о нехватке тестировщиков, Дементьева в нём явно не упомянута."),
            RelNegative("a4", "created_by", "e1", "решение a4 сформулировано безлично ('было решено') — текст не называет Волошина его автором в явном виде."),
            RelNegative("a5", "involves", "e3", "a5 — факт об уведомлении заказчика, к переговорной комнате (e3) отношения не имеет."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_project_decision_chain",
        split="calibration",
        domain="work",
        text=(
            "Совет проекта «Горизонт-2» в марте принял решение использовать поставщика А. "
            "В апреле совет пересмотрел выбор и принял новое решение — перейти на поставщика "
            "Б, которое заменило собой мартовское решение. Переход на поставщика Б потребовал "
            "пересмотра бюджета проекта. Итоговый бюджетный отчёт составлен на основе "
            "апрельского решения. В отчёте есть ссылка на мартовское решение совета."
        ),
        atoms=(
            RelAtom(ref="a1", kind="decision", canonical_text="Совет проекта «Горизонт-2» в марте принял решение использовать поставщика А."),
            RelAtom(ref="a2", kind="decision", canonical_text="Совет пересмотрел выбор и принял новое решение — перейти на поставщика Б."),
            RelAtom(ref="a3", kind="fact", canonical_text="Переход на поставщика Б потребовал пересмотра бюджета проекта."),
            RelAtom(ref="a4", kind="fact", canonical_text="Итоговый бюджетный отчёт составлен на основе апрельского решения."),
            RelAtom(ref="a5", kind="fact", canonical_text="В отчёте есть ссылка на мартовское решение совета."),
        ),
        entailed=(
            RelPositive("a2", "supersedes", "a1"),
            RelPositive("a2", "resulted_in", "a3"),
            RelPositive("a4", "derived_from", "a2"),
            RelPositive("a5", "refers_to", "a1"),
        ),
        not_entailed=(
            RelNegative("a1", "supersedes", "a2", "reversed_direction: новое решение a2 заменяет старое a1, не наоборот."),
            RelNegative("a4", "resulted_in", "a3", "reversed_direction по времени: пересмотр бюджета (a3) предшествует отчёту (a4), отчёт не мог 'привести' к a3."),
            RelNegative("a2", "derived_from", "a4", "reversed_direction: отчёт a4 составлен на основе решения a2, не наоборот."),
            RelNegative("a1", "refers_to", "a5", "reversed_direction: ссылку на решение делает отчёт a5, не мартовское решение a1."),
            RelNegative("a1", "resulted_in", "a3", "текст явно связывает пересмотр бюджета (a3) с апрельским решением a2 («переход … потребовал»), не с мартовским a1."),
            RelNegative("a4", "derived_from", "a1", "отчёт составлен на основе АПРЕЛЬСКОГО решения (a2), не мартовского (a1) — текст это явно уточняет."),
            RelNegative("a5", "refers_to", "a2", "ссылка сделана на МАРТОВСКОЕ решение (a1), не на апрельское (a2)."),
            RelNegative("a3", "refers_to", "a1", "a3 — факт о пересмотре бюджета, ссылки на мартовское решение текст не делает — эту ссылку делает отдельно a5."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_org_structure",
        split="calibration",
        domain="work",
        text=(
            "Отдел маркетинга входит в состав ООО «Ромашка». ООО «Ромашка», в свою очередь, "
            "принадлежит холдингу АО «Агро-Инвест». Руководитель отдела маркетинга Белова "
            "Ирина занимает должность директора по маркетингу. Оборудование отдела — три "
            "ноутбука — принадлежит ООО «Ромашка»."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="ORGANIZATION", label="Отдел маркетинга"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="ООО «Ромашка»"),
            RelEntity(ref="e3", entity_type="ORGANIZATION", label="АО «Агро-Инвест»"),
            RelEntity(ref="e4", entity_type="PERSON", label="Белова Ирина"),
            RelEntity(ref="e5", entity_type="CONCEPT", label="директор по маркетингу", subtype="role_concept"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="ООО «Ромашка» принадлежит холдингу АО «Агро-Инвест»."),
            RelAtom(ref="a2", kind="fact", canonical_text="Оборудование отдела — три ноутбука — принадлежит ООО «Ромашка»."),
        ),
        entailed=(
            RelPositive("e1", "part_of", "e2"),
            RelPositive("a1", "owned_by", "e3"),
            RelPositive("a2", "owned_by", "e2"),
            RelPositive("e4", "has_role", "e5"),
        ),
        not_entailed=(
            RelNegative("e2", "part_of", "e1", "reversed_direction."),
            RelNegative("e2", "part_of", "e3", "текст описывает отношение владения холдингом (owned_by), не структурное включение ООО «Ромашка» в холдинг как организационной части (part_of)."),
            RelNegative("a1", "owned_by", "e2", "a1 — факт о владении холдингом АО «Агро-Инвест» (e3), не ООО «Ромашка» собой."),
            RelNegative("a2", "owned_by", "e3", "оборудование принадлежит ООО «Ромашка» (e2) по тексту напрямую, холдинг e3 в этом предложении не упомянут."),
            RelNegative("e3", "has_role", "e5", "холдинг АО «Агро-Инвест» (e3) не назван текстом обладателем роли директора по маркетингу — эта роль закреплена за Беловой Ириной (e4)."),
            RelNegative("e1", "has_role", "e5", "роль директора по маркетингу текст закрепляет за Беловой Ириной (e4) лично, не за отделом (e1) как организацией."),
            RelNegative("a2", "created_by", "e2", "a2 — факт о владении оборудованием, не о том, что ООО «Ромашка» его 'создала' — created_by здесь текстом не подтверждён."),
            RelNegative("a1", "owned_by", "e1", "a1 говорит о владении холдингом (e3) над ООО «Ромашка» (e2), не об отделе маркетинга (e1) — отдел здесь не упомянут."),
            RelNegative("a2", "refers_to", "a1", "a2 — факт о владении оборудованием, ссылки на факт о владении холдингом (a1) не делает — разные, отдельно утверждённые факты."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_purchase_ownership",
        split="calibration",
        domain="purchases",
        text=(
            "Принтер HP куплен 3 марта 2026 года в магазине «Комус» и принадлежит ООО "
            "«Ромашка». Гарантийный талон на принтер выпущен производителем HP. Чек об "
            "оплате подтверждает факт покупки принтера. Покупка была оформлена в офисе на "
            "Садовой улице."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="ORGANIZATION", label="магазин «Комус»"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="ООО «Ромашка»"),
            RelEntity(ref="e3", entity_type="ORGANIZATION", label="HP"),
            RelEntity(ref="e4", entity_type="PLACE", label="офис на Садовой улице"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Принтер HP куплен 3 марта 2026 года в магазине «Комус» и принадлежит ООО «Ромашка»."),
            RelAtom(ref="a2", kind="fact", canonical_text="Гарантийный талон на принтер выпущен производителем HP."),
            RelAtom(ref="a3", kind="fact", canonical_text="Чек об оплате подтверждает факт покупки принтера."),
            RelAtom(ref="a4", kind="fact", canonical_text="Покупка была оформлена в офисе на Садовой улице."),
        ),
        entailed=(
            RelPositive("a1", "located_at", "e1"),
            RelPositive("a1", "owned_by", "e2"),
            RelPositive("a2", "created_by", "e3"),
            RelPositive("a3", "supports", "a1"),
            RelPositive("a4", "located_at", "e4"),
        ),
        not_entailed=(
            RelNegative("a1", "owned_by", "e1", "принтер принадлежит ООО «Ромашка» (e2); магазин «Комус» — продавец (located_at), не владелец после покупки."),
            RelNegative("a1", "located_at", "e2", "a1 куплен в магазине «Комус» (e1); ООО «Ромашка» — владелец, не место покупки."),
            RelNegative("a2", "created_by", "e2", "гарантийный талон выпущен производителем HP (e3), не ООО «Ромашка»."),
            RelNegative("a1", "supports", "a3", "reversed_direction: чек (a3) подтверждает факт покупки (a1), не наоборот."),
            RelNegative("a4", "located_at", "e1", "оформление покупки произошло в офисе на Садовой улице (e4), не в магазине «Комус» (e1) — два разных места по тексту."),
            RelNegative("a3", "created_by", "e3", "чек не назван текстом созданным производителем HP — HP выпустил гарантийный талон (a2), не чек (a3)."),
            RelNegative("a2", "located_at", "e1", "a2 — факт о выпуске гарантийного талона производителем, места не касается — located_at размечен только для a1/a4."),
            RelNegative("a4", "owned_by", "e2", "a4 — факт об оформлении покупки в офисе, не о владении — владение зафиксировано в a1."),
            RelNegative("a3", "located_at", "e1", "a3 — факт о чеке, места не упоминает — located_at размечен только для a1 (покупка) и a4 (оформление)."),
            RelNegative("a1", "created_by", "e1", "a1 — факт о покупке и владении, магазин «Комус» — не создатель принтера (created_by относится к производителю HP, a2)."),
            RelNegative("a2", "refers_to", "a1", "a2 — факт о выпуске гарантийного талона, явной ссылки на факт покупки (a1) текст не делает."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_finance_concepts",
        split="calibration",
        domain="personal",
        text=(
            "Статья была посвящена теме дефляции. Дефляция — понятие, тесно связанное с "
            "инфляцией как противоположный процесс. Один аналитик утверждает, что дефляция "
            "полезна для потребителей, тогда как другой аналитик утверждает обратное — что "
            "дефляция вредна для экономики в целом, и это второе мнение прямо противоречит "
            "первому. В экономике действительно происходит дефляция — это подтверждает "
            "статистика по снижению цен за квартал."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="CONCEPT", label="дефляция"),
            RelEntity(ref="e2", entity_type="CONCEPT", label="инфляция"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Статья была посвящена теме дефляции."),
            RelAtom(ref="a2", kind="fact", canonical_text="Один аналитик утверждает, что дефляция полезна для потребителей."),
            RelAtom(ref="a3", kind="fact", canonical_text="Другой аналитик утверждает, что дефляция вредна для экономики в целом."),
            RelAtom(ref="a4", kind="fact", canonical_text="Статистика по снижению цен за квартал зафиксирована."),
            RelAtom(ref="a5", kind="fact", canonical_text="В экономике действительно происходит дефляция."),
        ),
        entailed=(
            RelPositive("a1", "about", "e1"),
            RelPositive("e1", "related_to", "e2"),
            RelPositive("a3", "contradicts", "a2"),
            RelPositive("a4", "supports", "a5"),
        ),
        not_entailed=(
            RelNegative("a1", "about", "e2", "статья посвящена дефляции (e1), не инфляции (e2) — инфляция упомянута только для сравнения."),
            RelNegative("a2", "contradicts", "a5", "мнение о пользе дефляции (a2) не названо текстом противоречащим факту её наличия (a5) — противоречие текст фиксирует только между a2 и a3 (двумя мнениями аналитиков)."),
            RelNegative("a5", "supports", "a4", "reversed_direction: статистика (a4) подтверждает факт a5, не наоборот."),
            RelNegative("a4", "about", "e1", "about в этом кейсе размечена для a1 (статьи); a4 — статистика, отдельный факт, не переопределяет тему статьи."),
            RelNegative("a2", "supports", "a5", "мнение аналитика о пользе дефляции (a2) — оценочное суждение, не свидетельство её наличия; наличие дефляции подтверждает статистика (a4), не мнение a2."),
            RelNegative("a5", "about", "e2", "a5 утверждает наличие дефляции (e1), тема инфляции (e2) в нём не упомянута."),
            RelNegative("a3", "about", "e2", "a3 обсуждает вред дефляции, тему инфляции (e2) не затрагивает — about для e2 нигде текстом не подтверждается."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_lecture_series",
        split="calibration",
        domain="learning",
        text=(
            "На лекции по макроэкономике профессор Орлова Татьяна рассказала о понятии "
            "стагфляции. Стагфляция тесно связана с инфляцией, сочетая её с экономическим "
            "спадом. Орлова Татьяна — профессор кафедры экономики. Лекция также кратко "
            "затронула тему безработицы."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Орлова Татьяна"),
            RelEntity(ref="e2", entity_type="CONCEPT", label="стагфляция"),
            RelEntity(ref="e3", entity_type="CONCEPT", label="инфляция"),
            RelEntity(ref="e4", entity_type="CONCEPT", label="профессор кафедры экономики", subtype="role_concept"),
            RelEntity(ref="e5", entity_type="CONCEPT", label="безработица"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="event", canonical_text="На лекции по макроэкономике профессор Орлова Татьяна рассказала о понятии стагфляции."),
            RelAtom(ref="a2", kind="fact", canonical_text="Лекция также кратко затронула тему безработицы."),
        ),
        entailed=(
            RelPositive("a1", "involves", "e1"),
            RelPositive("a1", "about", "e2"),
            RelPositive("e2", "related_to", "e3"),
            RelPositive("e1", "has_role", "e4"),
            RelPositive("a2", "about", "e5"),
        ),
        not_entailed=(
            RelNegative("a1", "about", "e3", "лекция явно рассказывает о стагфляции (e2); инфляция упомянута лишь как часть определения стагфляции, отдельной темой a1 текст её не называет."),
            RelNegative("e1", "has_role", "e3", "инфляция (e3) — понятие, упомянутое в связи со стагфляцией, не профессиональная роль Орловой — её роль зафиксирована как e4."),
            RelNegative("a2", "involves", "e1", "a2 — отдельное предложение про тему безработицы, Орлова в нём не названа участником явно (в отличие от a1)."),
            RelNegative("a1", "about", "e5", "тему безработицы текст относит к a2 («также кратко затронула» — отдельное, более позднее предложение), не к a1."),
            RelNegative("e3", "related_to", "e5", "текст не утверждает связь между инфляцией и безработицей — упомянуты в разных, не связанных по тексту предложениях."),
            RelNegative("a2", "about", "e2", "тема безработицы (e5) — отдельная от стагфляции (e2), про которую говорит a1; a2 её не упоминает."),
            RelNegative("e1", "has_role", "e5", "e5 — тема «безработица», не понятие-роль, связанное с профессией Орловой — её роль зафиксирована как e4."),
            RelNegative("e4", "related_to", "e2", "роль-понятие 'профессор кафедры экономики' (e4) текстом не связывается с темой инфляции (e2) — разные, не связанные явно понятия."),
            RelNegative("a2", "about", "e3", "a2 — факт о безработице, тему инфляции (e3) не упоминает."),
            RelNegative("e5", "related_to", "e2", "безработица (e5) и стагфляция (e2) не названы текстом связанными понятиями — только стагфляция явно связана с инфляцией (e3)."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_legal_contract_dispute",
        split="calibration",
        domain="work",
        text=(
            "Первоначальный договор поставки предусматривал срок доставки 10 дней. Задержка "
            "на таможне возникла и стала причиной подписания дополнительного соглашения, "
            "которое заменило собой этот пункт договора, установив новый срок — 20 дней. "
            "Новый срок доставки привёл к пересмотру плана производства у покупателя. Один "
            "из менеджеров утверждает, что доставка укладывается в 10 дней, что прямо "
            "противоречит дополнительному соглашению. В переписке с покупателем есть ссылка "
            "на текст дополнительного соглашения."
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Первоначальный договор поставки предусматривал срок доставки 10 дней."),
            RelAtom(ref="a2", kind="decision", canonical_text="Стороны подписали дополнительное соглашение, установив новый срок — 20 дней."),
            RelAtom(ref="a3", kind="fact", canonical_text="Задержка на таможне возникла перед подписанием соглашения."),
            RelAtom(ref="a4", kind="fact", canonical_text="План производства у покупателя был пересмотрен."),
            RelAtom(ref="a5", kind="fact", canonical_text="Один из менеджеров утверждает, что доставка укладывается в 10 дней."),
            RelAtom(ref="a6", kind="fact", canonical_text="В переписке с покупателем есть ссылка на текст дополнительного соглашения."),
        ),
        entailed=(
            RelPositive("a2", "supersedes", "a1"),
            RelPositive("a3", "reason_for", "a2"),
            RelPositive("a2", "resulted_in", "a4"),
            RelPositive("a5", "contradicts", "a2"),
            RelPositive("a6", "refers_to", "a2"),
        ),
        not_entailed=(
            RelNegative("a1", "supersedes", "a2", "reversed_direction."),
            RelNegative("a1", "reason_for", "a2", "первоначальный договор (a1) — предмет замены, не причина подписания соглашения; причина — задержка на таможне (a3)."),
            RelNegative("a1", "resulted_in", "a4", "пересмотр плана производства (a4) вызван НОВЫМ сроком доставки, установленным соглашением a2, а не первоначальным договором a1."),
            RelNegative("a1", "contradicts", "a5", "менеджер утверждает про срок 10 дней, что совпадает с ПЕРВОНАЧАЛЬНЫМ договором (a1), а не противоречит ему — противоречие текст фиксирует именно с действующим доп.соглашением (a2)."),
            RelNegative("a2", "refers_to", "a6", "reversed_direction: ссылку на соглашение делает переписка (a6), не само соглашение (a2)."),
            RelNegative("a3", "supersedes", "a1", "a3 — факт о задержке (причина), не отдельное решение, способное заменить договор a1."),
            RelNegative("a3", "supersedes", "a2", "задержка (a3) — причина решения a2 (reason_for), не отдельное решение, заменяющее его (supersedes) — a3 не имеет статуса замены."),
            RelNegative("a6", "contradicts", "a1", "переписка (a6) лишь ссылается на текст соглашения (refers_to), не заявляет ничего, что противоречило бы первоначальному договору (a1)."),
            RelNegative("a5", "refers_to", "a1", "мнение менеджера (a5) не оформлено текстом как ссылка на договор — оно просто повторяет прежний срок, не цитируя документ."),
            RelNegative("a3", "refers_to", "a2", "задержка (a3) возникла ДО подписания соглашения (a2) — не может ссылаться на документ, которого ещё не существовало; вместо этого она — его причина (reason_for)."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_publication_chain",
        split="calibration",
        domain="work",
        text=(
            "Годовой отчёт по продажам подготовлен аналитическим отделом. Аналитический "
            "отдел входит в состав ООО «Вектор». Отчёт составлен на основе данных из "
            "CRM-системы за год. В отчёте есть ссылка на презентацию по итогам третьего "
            "квартала."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="ORGANIZATION", label="Аналитический отдел"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="ООО «Вектор»"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Годовой отчёт по продажам подготовлен аналитическим отделом."),
            RelAtom(ref="a2", kind="fact", canonical_text="Данные из CRM-системы за год были собраны и обработаны."),
            RelAtom(ref="a3", kind="fact", canonical_text="Презентация по итогам третьего квартала была представлена руководству."),
        ),
        entailed=(
            RelPositive("a1", "created_by", "e1"),
            RelPositive("e1", "part_of", "e2"),
            RelPositive("a1", "derived_from", "a2"),
            RelPositive("a1", "refers_to", "a3"),
        ),
        not_entailed=(
            RelNegative("a1", "created_by", "e2", "отчёт подготовлен именно аналитическим отделом (e1) как непосредственным автором; ООО «Вектор» — организация верхнего уровня, текст не называет её автором отчёта."),
            RelNegative("e2", "part_of", "e1", "reversed_direction."),
            RelNegative("a2", "derived_from", "a1", "reversed_direction: отчёт (a1) основан на данных CRM (a2), не наоборот."),
            RelNegative("a3", "refers_to", "a1", "reversed_direction: ссылку на презентацию делает отчёт (a1), не наоборот."),
            RelNegative("a2", "created_by", "e1", "a2 — факт о сборе данных CRM; CRM-данные не авторский документ, а исходный материал (об этом говорит derived_from, не created_by)."),
            RelNegative("a2", "refers_to", "a3", "a2 — факт о сборе данных CRM, ссылки на презентацию (a3) не делает — эту ссылку делает сам отчёт (a1)."),
            RelNegative("a3", "derived_from", "a2", "презентация (a3) упомянута только как ссылочный документ (refers_to из a1), текст не утверждает, что она построена на данных CRM (a2)."),
            RelNegative("a1", "owned_by", "e2", "a1 говорит об авторстве отчёта (created_by), не о владении им (owned_by) — эти два разных факта текст не смешивает."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_family_property",
        split="calibration",
        domain="personal",
        text=(
            "Дача в посёлке Сосновка принадлежит Кузнецову Петру. Урожай яблок в этом году "
            "был собран на участке дачи. Кузнецов Пётр увлекается ландшафтным дизайном на "
            "территории дачи. Тема садоводства тесно связана с темой ландшафтного дизайна."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Кузнецов Пётр"),
            RelEntity(ref="e2", entity_type="PLACE", label="дача в посёлке Сосновка"),
            RelEntity(ref="e3", entity_type="CONCEPT", label="садоводство"),
            RelEntity(ref="e4", entity_type="CONCEPT", label="ландшафтный дизайн"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Дача в посёлке Сосновка принадлежит Кузнецову Петру."),
            RelAtom(ref="a2", kind="fact", canonical_text="Урожай яблок в этом году был собран на участке дачи."),
            RelAtom(ref="a3", kind="fact", canonical_text="Кузнецов Пётр увлекается ландшафтным дизайном на территории дачи."),
        ),
        entailed=(
            RelPositive("a1", "owned_by", "e1"),
            RelPositive("a2", "located_at", "e2"),
            RelPositive("a3", "located_at", "e2"),
            RelPositive("a3", "involves", "e1"),
            RelPositive("a3", "about", "e4"),
            RelPositive("e3", "related_to", "e4"),
        ),
        not_entailed=(
            RelNegative("a2", "owned_by", "e1", "a2 описывает сбор урожая, не факт владения дачей — владение утверждает только a1."),
            RelNegative("a3", "owned_by", "e1", "a3 описывает деятельность (хобби), а не факт владения — владение утверждает только a1."),
            RelNegative("a3", "about", "e3", "a3 явно называет темой ландшафтный дизайн (e4); садоводство (e3) — отдельное, хоть и связанное понятие, но не тема именно этого предложения."),
            RelNegative("a2", "involves", "e1", "a2 сообщает про урожай, не упоминает Кузнецова Петра явно как участника события."),
            RelNegative("a1", "involves", "e1", "a1 — статичный факт владения, не описание события с активным участием (в отличие от a3)."),
            RelNegative("a2", "about", "e3", "a2 — факт о сборе урожая; текст явно не формулирует его как 'про тему садоводства' (about размечена только для a3→e4)."),
            RelNegative("a1", "about", "e3", "a1 — факт о владении дачей, темы садоводства (e3) не касается — about размечена только для a3→e4."),
            RelNegative("a1", "about", "e4", "a1 не упоминает ландшафтный дизайн (e4) — тема закреплена за a3."),
            RelNegative("a2", "about", "e4", "a2 — факт о сборе урожая, темы ландшафтного дизайна (e4) не касается — эта тема закреплена за a3."),
            RelNegative("e3", "related_to", "e1", "садоводство (e3) как понятие текстом не связывается с личностью Кузнецова (e1) отношением related_to — он лишь увлекается им (involves/about), это не понятийная связь двух тем."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_clinic_visit_specialty_2",
        split="final_holdout",
        domain="health",
        text=(
            "22 июня 2026 года в кабинете эндокринолога поликлиники №4 состоялся приём "
            "пациента Фомина Сергея у врача Лебедевой Ольги Николаевны. Приём касался темы "
            "сахарного диабета второго типа. Лебедева Ольга Николаевна — эндокринолог с "
            "пятнадцатилетним стажем."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Лебедева Ольга Николаевна"),
            RelEntity(ref="e2", entity_type="PERSON", label="Фомин Сергей"),
            RelEntity(ref="e3", entity_type="ORGANIZATION", label="поликлиника №4"),
            RelEntity(ref="e4", entity_type="CONCEPT", label="сахарный диабет второго типа"),
            RelEntity(ref="e5", entity_type="CONCEPT", label="эндокринолог", subtype="medical_specialty"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="event",
                   canonical_text="22 июня 2026 года в кабинете эндокринолога поликлиники №4 состоялся приём пациента Фомина Сергея у врача Лебедевой Ольги Николаевны."),
            RelAtom(ref="a2", kind="fact", canonical_text="Приём касался темы сахарного диабета второго типа."),
        ),
        entailed=(
            RelPositive("a1", "involves", "e1", role="doctor"),
            RelPositive("a1", "involves", "e2", role="patient"),
            RelPositive("a1", "located_at", "e3"),
            RelPositive("a2", "about", "e4"),
            RelPositive("e1", "has_role", "e5"),
        ),
        not_entailed=(
            RelNegative("e2", "has_role", "e4", "диагноз (e4) не является профессиональной ролью пациента Фомина (e2) — роль в этом кейсе зафиксирована только за Лебедевой (e1) её специальностью (e5)."),
            RelNegative("a2", "about", "e5", "приём касался темы диабета (e4), не специальности врача (e5) — специальность фиксирует has_role, не about этого факта."),
            RelNegative("e2", "has_role", "e5", "текст называет эндокринологом Лебедеву (e1), не пациента Фомина (e2)."),
            RelNegative("a2", "involves", "e2", "a2 — отдельный факт о теме приёма; участник (Фомин) назван в a1, в a2 явно не переутверждается."),
            RelNegative("a1", "about", "e4", "about относится к a2 (факту о теме), не к a1 (событию приёма)."),
            RelNegative("e1", "has_role", "e4", "e4 — диагноз/тема (сахарный диабет), не профессиональная специальность Лебедевой — её специальность e5."),
            RelNegative("a1", "about", "e5", "специальность эндокринолог (e5) — не тема события a1, а профессиональная роль Лебедевой (has_role)."),
            RelNegative("a2", "located_at", "e3", "a2 — факт о теме приёма, место (поликлиника №4) относится к событию a1, не переутверждается в a2."),
            RelNegative("a2", "involves", "e1", "a2 — факт о теме приёма, участников явно не называет — участие Лебедевой зафиксировано в a1."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_project_meeting_2",
        split="final_holdout",
        domain="work",
        text=(
            "Отдел логистики входит в состав ООО «Карго Плюс». 8 июля 2026 года руководитель "
            "отдела логистики Титов Игорь провёл совещание с водителями по поводу задержек "
            "поставок. Водителей не хватало, и это стало причиной решения нанять двух новых "
            "сотрудников. Найм новых сотрудников привёл к сокращению среднего времени "
            "доставки."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="ORGANIZATION", label="Отдел логистики"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="ООО «Карго Плюс»"),
            RelEntity(ref="e3", entity_type="PERSON", label="Титов Игорь"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="event", canonical_text="8 июля 2026 года руководитель отдела логистики Титов Игорь провёл совещание с водителями по поводу задержек поставок."),
            RelAtom(ref="a2", kind="fact", canonical_text="Водителей не хватало."),
            RelAtom(ref="a3", kind="decision", canonical_text="Было решено нанять двух новых сотрудников."),
            RelAtom(ref="a4", kind="fact", canonical_text="Среднее время доставки сократилось."),
        ),
        entailed=(
            RelPositive("e1", "part_of", "e2"),
            RelPositive("a1", "involves", "e3"),
            RelPositive("a2", "reason_for", "a3"),
            RelPositive("a3", "resulted_in", "a4"),
        ),
        not_entailed=(
            RelNegative("e2", "part_of", "e1", "reversed_direction."),
            RelNegative("a4", "reason_for", "a3", "a4 — следствие решения a3 (resulted_in), не его причина; причина решения — нехватка водителей (a2)."),
            RelNegative("a1", "resulted_in", "a4", "совещание (a1) не названо текстом напрямую приведшим к сокращению времени доставки (a4) — между ними стоит решение a3, которое и есть непосредственная причина a4."),
            RelNegative("a1", "involves", "e1", "a1 упоминает отдел логистики только как организационную принадлежность Титова, не как отдельного участника совещания наравне с людьми."),
            RelNegative("a2", "resulted_in", "a4", "нехватка водителей (a2) не названа текстом напрямую приведшей к сокращению времени доставки (a4) — между ними стоит решение a3."),
            RelNegative("a1", "resulted_in", "a2", "совещание (a1) не названо текстом причиной нехватки водителей (a2) — наоборот, нехватка предшествует и объясняет созыв совещания."),
            RelNegative("a2", "involves", "e3", "a2 — факт о нехватке водителей, Титов в нём явно не назван — его участие зафиксировано в a1."),
            RelNegative("a3", "involves", "e3", "решение a3 сформулировано безлично ('было решено') — текст не называет Титова его автором/участником явно."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_decision_supersede_2",
        split="final_holdout",
        domain="work",
        text=(
            "Первая версия регламента устанавливала лимит расходов в 50000 рублей. Вторая "
            "версия регламента заменила собой первую, установив лимит в 80000 рублей, и "
            "составлена на основе анализа фактических расходов за полугодие. Финансовый "
            "директор утверждает, что лимит остаётся прежним — 50000 рублей, что прямо "
            "противоречит второй версии регламента. Отчёт по фактическим расходам "
            "подтверждает обоснованность нового лимита."
        ),
        atoms=(
            RelAtom(ref="a1", kind="decision", canonical_text="Первая версия регламента устанавливала лимит расходов в 50000 рублей."),
            RelAtom(ref="a2", kind="decision", canonical_text="Вторая версия регламента установила лимит расходов в 80000 рублей."),
            RelAtom(ref="a3", kind="fact", canonical_text="Проведён анализ фактических расходов за полугодие."),
            RelAtom(ref="a4", kind="fact", canonical_text="Финансовый директор утверждает, что лимит остаётся прежним — 50000 рублей."),
            RelAtom(ref="a5", kind="fact", canonical_text="Отчёт по фактическим расходам подтверждает обоснованность нового лимита."),
        ),
        entailed=(
            RelPositive("a2", "supersedes", "a1"),
            RelPositive("a2", "derived_from", "a3"),
            RelPositive("a4", "contradicts", "a2"),
            RelPositive("a5", "supports", "a2"),
        ),
        not_entailed=(
            RelNegative("a1", "supersedes", "a2", "reversed_direction."),
            RelNegative("a3", "derived_from", "a2", "reversed_direction."),
            RelNegative("a4", "supports", "a2", "мнение финансового директора (a4) прямо ПРОТИВОРЕЧИТ второй версии (a2) — это отношение зафиксировано как contradicts, не supports."),
            RelNegative("a1", "contradicts", "a4", "a4 повторяет значение первой версии (a1) — они совпадают, не противоречат друг другу; противоречие текст фиксирует между a4 и действующей второй версией (a2)."),
            RelNegative("a4", "supersedes", "a2", "мнение финансового директора (a4) — не формально принятая версия регламента, способная заменить a2; текст не называет её решением с таким статусом."),
            RelNegative("a1", "derived_from", "a3", "анализ расходов (a3) стал основой именно ВТОРОЙ версии (a2) по тексту, не первой (a1)."),
            RelNegative("a3", "supports", "a1", "анализ расходов (a3) — основа ВТОРОЙ версии (a2, derived_from), не свидетельство в пользу ПЕРВОЙ версии (a1)."),
            RelNegative("a5", "contradicts", "a1", "текст явно фиксирует противоречие только между a4 и a2; a5 отдельно этого утверждения не делает — оно лишь подтверждает a2 (supports)."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_purchase_2",
        split="final_holdout",
        domain="purchases",
        text=(
            "Автомобиль Toyota Camry принадлежит Никитиной Елене. Инструкция по эксплуатации "
            "автомобиля выпущена производителем Toyota. Договор купли-продажи содержит "
            "ссылку на паспорт транспортного средства, оформленный при регистрации "
            "автомобиля. Тема технического обслуживания автомобиля тесно связана с темой "
            "безопасности дорожного движения."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Никитина Елена"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="Toyota"),
            RelEntity(ref="e3", entity_type="CONCEPT", label="техническое обслуживание автомобиля"),
            RelEntity(ref="e4", entity_type="CONCEPT", label="безопасность дорожного движения"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Автомобиль Toyota Camry принадлежит Никитиной Елене."),
            RelAtom(ref="a2", kind="fact", canonical_text="Инструкция по эксплуатации автомобиля выпущена производителем Toyota."),
            RelAtom(ref="a3", kind="fact", canonical_text="Договор купли-продажи содержит ссылку на паспорт транспортного средства."),
            RelAtom(ref="a4", kind="fact", canonical_text="Паспорт транспортного средства оформлен при регистрации автомобиля."),
        ),
        entailed=(
            RelPositive("a1", "owned_by", "e1"),
            RelPositive("a2", "created_by", "e2"),
            RelPositive("a3", "refers_to", "a4"),
            RelPositive("e3", "related_to", "e4"),
        ),
        not_entailed=(
            RelNegative("a2", "owned_by", "e2", "инструкция выпущена производителем (created_by) — это не факт владения автомобилем; владелец — Никитина Елена (a1)."),
            RelNegative("a1", "created_by", "e1", "a1 — факт о владении (owned_by), не об авторстве/создании — Никитина владеет автомобилем, не 'создала' его."),
            RelNegative("a4", "refers_to", "a3", "reversed_direction: ссылку на паспорт делает договор (a3), не наоборот."),
            RelNegative("a3", "refers_to", "a2", "договор ссылается на паспорт ТС (a4) по тексту, не на инструкцию по эксплуатации (a2) — разные документы."),
            RelNegative("a2", "created_by", "e1", "инструкцию выпустил производитель Toyota (e2), не владелица автомобиля Никитина (e1)."),
            RelNegative("a1", "refers_to", "a4", "a1 — факт о владении автомобилем, ссылки на паспорт ТС не делает — эту ссылку делает договор купли-продажи (a3)."),
            RelNegative("a2", "refers_to", "a4", "a2 — факт о выпуске инструкции производителем, паспорт ТС (a4) в нём не упоминается."),
            RelNegative("e3", "related_to", "e1", "тема техобслуживания (e3) не связана текстом с личностью Никитиной (e1) — она лишь владелица автомобиля, не связанное понятие."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_supply_chain_review",
        split="calibration",
        domain="work",
        text=(
            "Отдел закупок входит в состав ООО «Вектор Снаб». Отчёт по закупкам за квартал "
            "подготовлен отделом закупок; в отчёте есть ссылка на договор с поставщиком "
            "металлопроката. Складское оборудование принадлежит ООО «Вектор Снаб»."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="ORGANIZATION", label="Отдел закупок"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="ООО «Вектор Снаб»"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Отчёт по закупкам за квартал подготовлен отделом закупок; в отчёте есть ссылка на договор с поставщиком металлопроката."),
            RelAtom(ref="a2", kind="fact", canonical_text="Складское оборудование принадлежит ООО «Вектор Снаб»."),
            RelAtom(ref="a3", kind="fact", canonical_text="Договор с поставщиком металлопроката зафиксировал условия поставки."),
        ),
        entailed=(
            RelPositive("e1", "part_of", "e2"),
            RelPositive("a1", "created_by", "e1"),
            RelPositive("a2", "owned_by", "e2"),
            RelPositive("a1", "refers_to", "a3"),
        ),
        not_entailed=(
            RelNegative("e2", "part_of", "e1", "reversed_direction."),
            RelNegative("a1", "created_by", "e2", "отчёт подготовлен отделом закупок (e1) как непосредственным автором, не организацией верхнего уровня ООО «Вектор Снаб» (e2)."),
            RelNegative("a2", "owned_by", "e1", "складское оборудование принадлежит ООО «Вектор Снаб» (e2) по тексту, не отделу закупок (e1) отдельно."),
            RelNegative("a3", "refers_to", "a1", "reversed_direction: ссылку на договор делает отчёт (a1), не наоборот."),
            RelNegative("a2", "created_by", "e1", "a2 — факт о владении оборудованием, не об авторстве/создании."),
            RelNegative("a1", "owned_by", "e2", "a1 — факт об авторстве отчёта (created_by), не о владении."),
            RelNegative("a2", "refers_to", "a3", "a2 — факт о владении складским оборудованием, ссылки на договор с поставщиком (a3) не делает — эту ссылку делает отчёт (a1)."),
            RelNegative("a3", "created_by", "e1", "a3 — факт о договоре с поставщиком, текст не называет отдел закупок (e1) его автором — авторство относится к отчёту (a1), не к самому договору."),
            RelNegative("a1", "located_at", "e2", "a1 — факт об авторстве и содержании отчёта, места текст не упоминает — located_at здесь не установлен."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_research_conclusions",
        split="calibration",
        domain="work",
        text=(
            "Пилотное тестирование показало высокий процент брака, и это стало причиной "
            "решения остановить производственную линию. Из-за остановки линии производство "
            "простаивало два дня. Повторные замеры подтверждают, что процент брака "
            "действительно был высоким. Первоначальный отчёт по качеству утверждал, что брак "
            "находится в пределах нормы, что прямо противоречит результатам пилотного "
            "тестирования. Итоговый отчёт по качеству зафиксировал реальный высокий процент "
            "брака и заменил собой первоначальный отчёт."
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Пилотное тестирование показало высокий процент брака."),
            RelAtom(ref="a2", kind="decision", canonical_text="Было решено остановить производственную линию."),
            RelAtom(ref="a3", kind="fact", canonical_text="Производство простаивало два дня."),
            RelAtom(ref="a4", kind="fact", canonical_text="Повторные замеры подтверждают, что процент брака действительно был высоким."),
            RelAtom(ref="a5", kind="fact", canonical_text="Первоначальный отчёт по качеству утверждал, что брак находится в пределах нормы."),
            RelAtom(ref="a6", kind="fact", canonical_text="Итоговый отчёт по качеству зафиксировал реальный высокий процент брака."),
        ),
        entailed=(
            RelPositive("a1", "reason_for", "a2"),
            RelPositive("a2", "resulted_in", "a3"),
            RelPositive("a4", "supports", "a1"),
            RelPositive("a5", "contradicts", "a1"),
            RelPositive("a6", "supersedes", "a5"),
        ),
        not_entailed=(
            RelNegative("a4", "reason_for", "a2", "причина решения остановить линию — исходный результат пилотного тестирования (a1), не повторные замеры (a4), которые лишь подтверждают его позже."),
            RelNegative("a4", "resulted_in", "a3", "повторные замеры (a4) не названы текстом причиной простоя (a3) — простой вызван решением остановить линию (a2), а не замерами."),
            RelNegative("a1", "supports", "a4", "reversed_direction: повторные замеры (a4) подтверждают пилотный результат (a1), не наоборот."),
            RelNegative("a4", "contradicts", "a5", "a4 (повторные замеры) не названы текстом стороной прямого противоречия — оно зафиксировано между a5 и a1 (пилотным тестированием); a4 лишь независимо подтверждает a1."),
            RelNegative("a5", "supersedes", "a6", "reversed_direction: итоговый отчёт (a6) заменяет первоначальный (a5), не наоборот."),
            RelNegative("a1", "supersedes", "a5", "a1 — факт о результатах пилотного тестирования, не отдельный 'отчёт по качеству', способный формально заменить a5 — эту роль текст отводит a6."),
            RelNegative("a6", "contradicts", "a1", "итоговый отчёт (a6) подтверждает результат пилотного тестирования (a1) тем же выводом — противоречие текст фиксирует только между a5 и a1, не a6 и a1."),
            RelNegative("a5", "supersedes", "a1", "a5 — противоречащее утверждение (contradicts), не формальная замена результатов пилотного тестирования (supersedes применим только к a6→a5)."),
            RelNegative("a3", "reason_for", "a2", "простой (a3) — следствие решения a2 (resulted_in), возник ПОСЛЕ него — не может быть его причиной."),
            RelNegative("a6", "derived_from", "a5", "a6 заменяет a5 (supersedes) по тексту явно; о том, что a6 составлен НА ОСНОВЕ a5 (derived_from), текст не говорит."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_editorial_workflow",
        split="calibration",
        domain="work",
        text=(
            "Редакция новостного отдела входит в состав издательского дома «Медиа Групп». "
            "Итоговая статья написана редактором Соколовой Верой на основе черновика, "
            "подготовленного стажёром. Черновик, в свою очередь, основан на исходном "
            "пресс-релизе компании-заказчика. Тема статьи — цифровая трансформация — тесно "
            "связана с темой автоматизации бизнес-процессов."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="ORGANIZATION", label="Редакция новостного отдела"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="издательский дом «Медиа Групп»"),
            RelEntity(ref="e3", entity_type="PERSON", label="Соколова Вера"),
            RelEntity(ref="e4", entity_type="CONCEPT", label="цифровая трансформация"),
            RelEntity(ref="e5", entity_type="CONCEPT", label="автоматизация бизнес-процессов"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Итоговая статья написана редактором Соколовой Верой."),
            RelAtom(ref="a2", kind="fact", canonical_text="Стажёр подготовил черновик статьи."),
            RelAtom(ref="a3", kind="fact", canonical_text="Исходный пресс-релиз компании-заказчика содержал основные факты."),
        ),
        entailed=(
            RelPositive("e1", "part_of", "e2"),
            RelPositive("a1", "created_by", "e3"),
            RelPositive("a1", "derived_from", "a2"),
            RelPositive("a2", "derived_from", "a3"),
            RelPositive("e4", "related_to", "e5"),
        ),
        not_entailed=(
            RelNegative("e2", "part_of", "e1", "reversed_direction."),
            RelNegative("a1", "created_by", "e1", "статья написана конкретным редактором Соколовой Верой (e3), не отделом (e1) как таковым — текст называет автором именно человека."),
            RelNegative("a2", "derived_from", "a1", "reversed_direction: итоговая статья (a1) основана на черновике (a2), не наоборот."),
            RelNegative("a3", "derived_from", "a2", "reversed_direction: черновик (a2) основан на пресс-релизе (a3), не наоборот."),
            RelNegative("a1", "derived_from", "a3", "текст описывает цепочку a3→a2→a1 (пресс-релиз → черновик → статья), а не прямую связь a1 напрямую с a3, минуя черновик."),
            RelNegative("e4", "related_to", "e3", "тема цифровой трансформации не связана текстом с личностью Соколовой Веры — она лишь автор статьи об этой теме, не связанное понятие."),
            RelNegative("a3", "created_by", "e3", "пресс-релиз (a3) — исходный материал компании-заказчика; Соколова (e3) — автор итоговой статьи (a1), не пресс-релиза."),
            RelNegative("a2", "created_by", "e3", "черновик (a2) подготовлен стажёром, не редактором Соколовой — её авторство относится к итоговой статье (a1)."),
            RelNegative("a1", "refers_to", "a3", "текст описывает производную цепочку (derived_from: a3→a2→a1), не факт явной ссылки — refers_to для этой пары текстом не установлен."),
            RelNegative("e5", "related_to", "e3", "тема автоматизации бизнес-процессов (e5) не связана текстом с личностью Соколовой Веры (e3) — она автор статьи, не понятие."),
            RelNegative("a3", "refers_to", "a2", "пресс-релиз (a3) существовал до черновика (a2) — не может ссылаться на документ, которого тогда не было; напротив, черновик основан на пресс-релизе (derived_from)."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_weather_delay_chain",
        split="calibration",
        domain="personal",
        text=(
            "Начался сильный снегопад, и это стало причиной решения перенести рейс на "
            "следующий день. Из-за переноса рейса бронь отеля пришлось продлить. Данные "
            "метеослужбы подтверждают, что снегопад был аномально сильным. Один из "
            "пассажиров утверждает, что снегопад был обычным для этого сезона, что прямо "
            "противоречит данным метеослужбы. Позже авиакомпания выпустила новое расписание, "
            "которое заменило собой перенесённый рейс ещё одной датой."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="CONCEPT", label="аномальные снегопады"),
            RelEntity(ref="e2", entity_type="CONCEPT", label="изменение климата"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Начался сильный снегопад."),
            RelAtom(ref="a2", kind="decision", canonical_text="Было решено перенести рейс на следующий день."),
            RelAtom(ref="a3", kind="fact", canonical_text="Бронь отеля пришлось продлить."),
            RelAtom(ref="a4", kind="fact", canonical_text="Данные метеослужбы подтверждают, что снегопад был аномально сильным."),
            RelAtom(ref="a5", kind="fact", canonical_text="Один из пассажиров утверждает, что снегопад был обычным для этого сезона."),
            RelAtom(ref="a6", kind="decision", canonical_text="Авиакомпания выпустила новое расписание с ещё одной датой рейса."),
        ),
        entailed=(
            RelPositive("a1", "reason_for", "a2"),
            RelPositive("a2", "resulted_in", "a3"),
            RelPositive("a4", "supports", "a1"),
            RelPositive("a5", "contradicts", "a4"),
            RelPositive("a6", "supersedes", "a2"),
            RelPositive("e1", "related_to", "e2"),
        ),
        not_entailed=(
            RelNegative("a4", "reason_for", "a2", "причина решения о переносе рейса — сам факт начавшегося снегопада (a1), не данные метеослужбы (a4), которые лишь подтверждают его силу отдельно."),
            RelNegative("a4", "resulted_in", "a3", "данные метеослужбы (a4) не названы текстом причиной продления брони (a3) — продление вызвано решением о переносе рейса (a2)."),
            RelNegative("a1", "supports", "a4", "reversed_direction: данные метеослужбы (a4) подтверждают факт снегопада (a1), не наоборот."),
            RelNegative("a1", "contradicts", "a5", "мнение пассажира (a5) противоречит именно ДАННЫМ метеослужбы об аномальности (a4), а не самому факту снегопада (a1) — a1 лишь констатирует, что снегопад был, без оценки его силы."),
            RelNegative("a2", "supersedes", "a6", "reversed_direction: новое расписание (a6) заменяет прежнее решение о переносе (a2), не наоборот."),
            RelNegative("a3", "related_to", "a4", "продление брони отеля (a3) и данные метеослужбы (a4) не названы текстом связанными напрямую сверх причинно-следственной цепочки через a1/a2."),
            RelNegative("a3", "contradicts", "a4", "продление брони (a3) не противоречит данным метеослужбы (a4) — разные, не конфликтующие факты."),
            RelNegative("a6", "contradicts", "a1", "новое расписание (a6) не противоречит факту снегопада (a1) — оно лишь заменяет более раннее решение a2 (supersedes)."),
            RelNegative("a5", "supersedes", "a2", "мнение пассажира (a5) — не формальное решение, способное заменить a2; отношение a5 к тексту — contradicts (с a4), не supersedes."),
            RelNegative("a4", "reason_for", "a6", "текст не называет данные метеослужбы (a4) причиной выпуска нового расписания (a6) — a6 представлен просто как более позднее решение, заменяющее a2."),
            RelNegative("a3", "refers_to", "a1", "продление брони (a3) — следствие решения a2 (resulted_in), не факт явной ссылки на снегопад (a1) — refers_to здесь не установлен текстом."),
            RelNegative("a3", "related_to", "a5", "продление брони (a3) и мнение пассажира о силе снегопада (a5) не названы текстом связанными — разные, не пересекающиеся утверждения."),
        ),
    ),
)

