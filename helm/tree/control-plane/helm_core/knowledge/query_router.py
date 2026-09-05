"""R7 — маршрутизатор вопроса: ответ только из доказанного.

Распоряжение владельца 05.09.2026:

> R7 обязан поддерживать два пути. GRAPH — только по уже доказанным
> рёбрам. EVIDENCE_FALLBACK — каноническая личность с составом ≥1 →
> состав → упоминания → точный провенанс → доказательство. Личности без
> состава в ответах не участвуют никогда. Первая приёмка — «каких врачей
> я посещал?»: ФИО, специальность, дата/контекст если доказаны,
> источник. Если ФИО есть, а специальность не доказана — «ФИО —
> специальность не подтверждена». Никаких inference-догадок по названию
> клиники, отделению или контексту. Пустой GRAPH — не ошибка.

**Почему два пути, а не один.** R5 показал измерением: на реальных
выписках компилятор связей даёт ноль рёбер (`pairs 33, grounded 0`) —
цитата атома и цитата сущности там не пересекаются вовсе. Ответ по обходу
графа на этом материале невозможен, но провенанс есть у каждого узла, и
он точный. Пустой граф поэтому не ошибка и не повод чинить компилятор
(это отдельное решение владельца), а штатная ветка.

**Что здесь считается доказательством.** Собственная цитата сущности —
диапазон символов в тексте источника, записанный при публикации
(`knowledge_node_mentions.char_start/char_end`). Не окно, не абзац рядом,
не документ целиком. Врачебность признаётся ровно правилом компилятора
(`_DOCTOR_MARKER_RE` в пределах `_ROLE_PROXIMITY_TOKENS` токенов перед
подписью) — то же правило, что аттестовано в R4, применённое к
собственной цитате сущности вместо цитаты атома. Специальность — только
из дефисного маркера («врач-уролог») в той же цитате.

Название клиники, отделение и соседний текст доказательством не
являются. «Иванов упомянут в выписке урологического отделения» не делает
Иванова урологом, и такого вывода здесь нет.

Имена в стандартный вывод не печатаются: ответ пишется файлом, наружу
идут числа и флаги доказанности (§5.2 CLAUDE.md).
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from .health_schema import health_schema_configured, health_session, is_health_domain
from .relation_compiler import (
    _DOCTOR_MARKER_RE, _ROLE_PROXIMITY_TOKENS, _first_mention_index, _token_span,
    _tokens,
)
from .semantic_pilot import source_text
from .semantic_publish import HEALTH_MODELS, PUBLIC_MODELS
from .tenancy import bind_knowledge_user
from ..config import get_settings
from ..models import KnowledgeSource
from ..models.base import SemanticNodeKind, SemanticNodeStatus, SemanticRelationType


class QuestionIntent:
    DOCTORS_VISITED = "doctors_visited"
    UNSUPPORTED = "unsupported"


class AnswerPath:
    GRAPH = "graph"
    EVIDENCE = "evidence"
    #: Часть ответа пришла рёбрами, часть — доказательствами. §14.12
    #: требует именно этого, когда покрытие графа неполно: «merge only
    #: evidence-backed additions into the answer and label internally
    #: which part came from fallback». Выбирать один путь целиком —
    #: значит либо потерять доказанное, либо соврать про полноту.
    MIXED = "graph+evidence"
    NONE = "none"


#: Вопрос распознаётся двумя условиями сразу: врачебное слово И слово о
#: посещении. Одного «врача» мало — «что сказал врач про давление» это
#: другой вопрос, и отвечать на него этим исполнителем нельзя.
_DOCTOR_WORD_RE = re.compile(r"врач", re.IGNORECASE)
_VISIT_WORD_RE = re.compile(r"посеща|посетил|посещал|был у|ходил|приём|прием|наблюда",
                            re.IGNORECASE)

#: Дефисный маркер специальности. Токенизатор компилятора режет дефис
#: («врач-уролог» → «врач», «уролог»), поэтому связь между маркером и
#: специальностью видна только в сыром тексте; отсюда отдельное
#: выражение, а не разбор токенов.
_HYPHENATED_SPECIALTY_RE = re.compile(r"врач[а-яё]*-([а-яё]+)", re.IGNORECASE)

_PERSON_TYPES = ("PERSON", "person")

#: Сколько символов перед спаном читать в диагностике. Три токена
#: русского текста с запасом: «врача-нефролога» это 15 символов.
_MARKER_LOOKBEHIND_CHARS = 60


@dataclass
class Proof:
    """Одно доказательство: точное место в источнике и что там написано.

    На пути по графу точных границ нет — доказательством там служит само
    ребро, а его провенанс это источник и ребро целиком. Поэтому границы
    и цитата допускают `None`, а не подставное число: диапазон «-1» или
    границы окна выглядели бы как точное место, не будучи им (та же
    ошибка, которую R5 закрыл в упоминаниях).
    """

    source_id: str
    window_id: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    #: Текст спана. Уходит только в файл ответа, не в стандартный вывод.
    quote: str | None = None
    #: Ребро, если доказательство пришло путём графа.
    edge_id: str | None = None


@dataclass
class DoctorItem:
    identity_id: str
    person: str
    #: Пусто — «специальность не подтверждена». Список, а не строка:
    #: две разные доказанные специальности это факт документов, а не
    #: повод выбрать одну.
    specialties: list[str] = field(default_factory=list)
    #: Даты приёмов. Список, а не одно значение: два визита к одному
    #: врачу — две даты, и выбрать из них одну значило бы соврать. На
    #: пути доказательств список всегда пуст: у узла-сущности даты нет,
    #: а дата документа — не дата приёма.
    dates: list[str] = field(default_factory=list)
    proofs: list[Proof] = field(default_factory=list)
    path: str = AnswerPath.EVIDENCE

    def line(self) -> str:
        if self.specialties:
            return f"{self.person} — {', '.join(self.specialties)}"
        return f"{self.person} — специальность не подтверждена"

    def as_public_dict(self) -> dict:
        """Без имени и без цитат: то, что можно печатать в лог."""
        return {"identity_id": self.identity_id,
                "specialty_proven": bool(self.specialties),
                "date_proven": bool(self.dates),
                "proofs": len(self.proofs),
                "path": self.path}


@dataclass
class DoctorsAnswer:
    question: str
    intent: str
    path_used: str = AnswerPath.NONE
    #: Сколько доказанных врачебных рёбер нашлось. Ноль — не ошибка, а
    #: измеренное состояние графа на этом корпусе (R5).
    graph_edges: int = 0
    items: list[DoctorItem] = field(default_factory=list)
    #: Почему узел или личность не попали в ответ. Не «отладка», а часть
    #: ответа: «нашлось трое» без «двое отброшены за отсутствием
    #: доказательства» — неполная правда (§5.1).
    skipped: dict[str, int] = field(default_factory=dict)
    #: Что вообще рассматривалось. Без этих чисел «один врач» не
    #: отличить от «один врач из одного» и от «один из шестидесяти».
    considered: dict[str, int] = field(default_factory=dict)
    #: Личности-люди с составом, не давшие ни одного пункта ответа ни
    #: одним путём. Не ошибка и не пропуск: это люди, про которых в
    #: документах нет доказательства врачебной роли. Число обязано стоять
    #: рядом с ответом, иначе «трое врачей» читается как «в документах
    #: трое людей».
    uncovered_identities: int = 0

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def count(self, what: str, howmany: int = 1) -> None:
        self.considered[what] = self.considered.get(what, 0) + howmany

    def by_path(self, path: str) -> int:
        return sum(1 for item in self.items if item.path == path)

    def as_dict(self) -> dict:
        """Полный ответ — с именами и цитатами. Только в файл."""
        return {"question": self.question, "intent": self.intent,
                "path_used": self.path_used, "graph_edges": self.graph_edges,
                "items_from_graph": self.by_path(AnswerPath.GRAPH),
                "items_from_evidence": self.by_path(AnswerPath.EVIDENCE),
                "uncovered_identities": self.uncovered_identities,
                "considered": self.considered, "skipped": self.skipped,
                "answer": [item.line() for item in self.items],
                "items": [asdict(item) for item in self.items]}

    def as_public_dict(self) -> dict:
        """То же без содержимого: числа и флаги."""
        return {"intent": self.intent, "path_used": self.path_used,
                "graph_edges": self.graph_edges, "items": len(self.items),
                "items_from_graph": self.by_path(AnswerPath.GRAPH),
                "items_from_evidence": self.by_path(AnswerPath.EVIDENCE),
                "uncovered_identities": self.uncovered_identities,
                "items_with_specialty": sum(1 for i in self.items if i.specialties),
                "items_with_date": sum(1 for i in self.items if i.dates),
                "proofs_total": sum(len(i.proofs) for i in self.items),
                "considered": self.considered, "skipped": self.skipped,
                "by_item": [item.as_public_dict() for item in self.items]}


def detect_intent(question: str) -> str:
    if _DOCTOR_WORD_RE.search(question) and _VISIT_WORD_RE.search(question):
        return QuestionIntent.DOCTORS_VISITED
    return QuestionIntent.UNSUPPORTED


def doctor_proof(span_text: str, label: str) -> tuple[bool, str | None]:
    """Доказывает ли собственная цитата сущности, что это врач.

    Правило берётся у компилятора связей целиком, а не пересказывается:
    маркер «врач*» среди `_ROLE_PROXIMITY_TOKENS` токенов НЕПОСРЕДСТВЕННО
    перед подписью. Пересказ разошёлся бы с аттестованным в R4 правилом
    при первой же правке одного из двух мест.

    Возвращает (врач ли, специальность или None). Специальность
    признаётся только дефисной («врач-уролог») и только если слово после
    дефиса стоит в том же окне перед подписью: «уролог» из соседнего
    предложения к этому человеку не относится.
    """
    pos = _first_mention_index(span_text, label)
    if pos <= 0:
        return False, None
    tokens = _tokens(span_text)
    window_before = tokens[max(0, pos - _ROLE_PROXIMITY_TOKENS):pos]
    if not any(_DOCTOR_MARKER_RE.fullmatch(t) for t in window_before):
        return False, None
    for match in _HYPHENATED_SPECIALTY_RE.finditer(span_text):
        specialty = match.group(1).lower()
        if specialty in window_before:
            return True, specialty
    return True, None


def marker_precedes_span(text: str, char_start: int) -> bool:
    """Стоит ли врачебный маркер в тексте ИСТОЧНИКА прямо перед спаном.

    Только диагностика. Извлекатель отдаёт цитату сам, и она может
    начаться ровно с фамилии, отрезав «врач-уролог» на предыдущем
    символе; тогда доказательство есть в документе, но не в цитате. Это
    число отвечает на вопрос «почему ноль», и в ответ ничего не
    добавляет: расширять правило доказательства без решения владельца
    нельзя.
    """
    before = text[max(0, char_start - _MARKER_LOOKBEHIND_CHARS):char_start]
    tail = _tokens(before)[-_ROLE_PROXIMITY_TOKENS:]
    return any(_DOCTOR_MARKER_RE.fullmatch(t) for t in tail)


def marker_follows_label(span_text: str, label: str) -> bool:
    """Стоит ли врачебный маркер ПОСЛЕ подписи, в пределах того же окна.

    Тоже только диагностика. «Иванов И.И., врач-уролог» — обычная
    подпись под выпиской, и она столь же явна, как «врач-уролог Иванов
    И.И.», но аттестованное в R4 правило смотрит только назад. Число
    отвечает на вопрос, теряет ли строгое правило настоящих врачей;
    расширять правило без решения владельца нельзя, и ответ этот случай
    не получает.
    """
    span = _token_span(span_text, label)
    if span is None:
        return False
    tokens = _tokens(span_text)
    after = tokens[span[1] + 1:span[1] + 1 + _ROLE_PROXIMITY_TOKENS]
    return any(_DOCTOR_MARKER_RE.fullmatch(t) for t in after)


class _SourceTexts:
    """Тексты источников, читаемые по мере надобности.

    Раньше `answer_doctors_visited()` читала с диска ВСЕ источники сразу,
    ещё до первого запроса к графу. На пилотных восьми это ничего не
    стоило; в пути живого вопроса, после backfill всего корпуса, это
    означало бы чтение всех файлов владельца на каждый заданный вопрос.
    Нужны единицы — читаем единицы.
    """

    def __init__(self, answer: DoctorsAnswer) -> None:
        self._sources: dict[uuid.UUID, object] = {}
        self._cache: dict[uuid.UUID, str | None] = {}
        self._answer = answer

    def add(self, source) -> None:
        self._sources[source.id] = source

    def get(self, source_id, default=None):
        if source_id not in self._cache:
            source = self._sources.get(source_id)
            text = source_text(source) if source is not None else None
            if text is None:
                self._answer.skip("текст источника недоступен")
            self._cache[source_id] = text
        text = self._cache[source_id]
        return default if text is None else text


def _marker_specialty(spans: list[tuple[object, str]], label: str) -> list[str]:
    """Специальности, подтверждённые дефисным маркером в СОБСТВЕННЫХ цитатах.

    Одно правило на оба пути. Путь графа не получает своего, более
    мягкого: иначе один и тот же человек назывался бы урологом по ребру
    и «специальность не подтверждена» по доказательству, в зависимости
    от того, каким путём до него дошли.
    """
    found: list[str] = []
    for _mention, span in spans:
        is_doctor, specialty = doctor_proof(span, label)
        if is_doctor and specialty and specialty not in found:
            found.append(specialty)
    return found


def _spans_for(graph, models, *, tenant_id: uuid.UUID, node_id: uuid.UUID,
               text_by_source: dict[uuid.UUID, str],
               answer: DoctorsAnswer) -> list[tuple[object, str]]:
    """Упоминания узла с точным диапазоном и текстом этого диапазона."""
    spans: list[tuple[object, str]] = []
    mentions = graph.scalars(
        select(models.mention)
        .where(models.mention.knowledge_user_id == tenant_id,
               models.mention.node_id == node_id,
               models.mention.char_start.is_not(None))
        .order_by(models.mention.char_start)).all()
    if not mentions:
        answer.skip("узел без точного спана")
        return spans
    answer.count("упоминания с точным спаном", len(mentions))
    for mention in mentions:
        text = text_by_source.get(mention.source_id)
        if text is None:
            answer.skip("упоминание в источнике без текста")
            continue
        spans.append((mention, text[mention.char_start:mention.char_end]))
    return spans


def _graph_doctors(graph, models, *, tenant_id: uuid.UUID, run_ids: set[uuid.UUID],
                   identity_by_node: dict[uuid.UUID, uuid.UUID],
                   text_by_source: dict[uuid.UUID, str],
                   answer: DoctorsAnswer) -> list[DoctorItem]:
    """Путь по графу: только уже доказанные рёбра, ничего не выводя.

    `EVENT --INVOLVES(role=doctor)--> PERSON` даёт человека и дату
    события. Рёбер может не быть — тогда путь пуст, и это не ошибка.

    **Ответ считается по личностям, а не по рёбрам.** Два визита к
    одному врачу это два ребра и один врач; складывать их в два пункта
    значило бы отвечать числом посещений на вопрос о врачах.

    **Узел без канонической личности в ответ не попадает.** Личности у
    него нет не случайно: R6 отказался её назначить (однословная
    подпись, конфликт типа). Пропустить такой узел в ответ значило бы
    обойти решение R6 с другой стороны.

    **`HAS_ROLE` специальностью сам по себе не является.** Реестр связей
    §14.9 доменно-агностичен, и та же связь описывает «руководителя
    проекта». На смешанном корпусе R10 врач легко окажется ещё и
    руководителем, и это не должно стать его специальностью. Компилятор
    при этом не трогается — фильтр стоит здесь, в слое запроса.
    """
    rows = graph.execute(
        select(models.edge, models.node)
        .join(models.node, models.node.id == models.edge.to_node_id)
        .where(models.edge.knowledge_user_id == tenant_id,
               models.edge.relation_type == SemanticRelationType.INVOLVES.value,
               models.edge.role == "doctor",
               models.edge.semantic_run_id.in_(run_ids),
               models.node.status == SemanticNodeStatus.ACTIVE)).all()
    answer.graph_edges += len(rows)
    if not rows:
        return []

    roles: dict[uuid.UUID, list[str]] = {}
    for person_id, concept_label in graph.execute(
            select(models.edge.from_node_id, models.node.canonical_label)
            .join(models.node, models.node.id == models.edge.to_node_id)
            .where(models.edge.knowledge_user_id == tenant_id,
                   models.edge.relation_type == SemanticRelationType.HAS_ROLE.value,
                   models.edge.semantic_run_id.in_(run_ids))).all():
        roles.setdefault(person_id, []).append(concept_label)

    by_identity: dict[str, DoctorItem] = {}
    seen_roles: set[tuple[uuid.UUID, str]] = set()
    #: Узлы, чьи цитаты уже прочитаны. Ключ группировки — личность, но
    #: доказательства собираются с КАЖДОГО её узла: один и тот же врач
    #: приходит двумя узлами из разных документов, и специальность
    #: вполне может быть доказана только во втором. Читать цитаты одного
    #: узла повторно (у него бывает несколько рёбер) при этом незачем.
    scanned: set[uuid.UUID] = set()
    for edge, person in rows:
        identity_id = identity_by_node.get(person.id)
        if identity_id is None:
            answer.skip("узел графа без канонической личности")
            continue
        key = str(identity_id)
        item = by_identity.get(key)
        if item is None:
            item = DoctorItem(identity_id=key, person=person.canonical_label,
                              path=AnswerPath.GRAPH)
            by_identity[key] = item

        if person.id not in scanned:
            scanned.add(person.id)
            for specialty in _marker_specialty(
                    _spans_for(graph, models, tenant_id=tenant_id, node_id=person.id,
                               text_by_source=text_by_source, answer=answer),
                    person.canonical_label):
                if specialty not in item.specialties:
                    item.specialties.append(specialty)
            for concept in roles.get(person.id, ()):
                if concept.lower() in item.specialties:
                    continue
                if (person.id, concept) in seen_roles:
                    continue
                seen_roles.add((person.id, concept))
                answer.skip("роль из графа не подтверждена как медицинская специальность")

        event = graph.get(models.node, edge.from_node_id)
        occurred = event.occurred_at_start if event is not None else None
        if occurred is not None:
            date = occurred.date().isoformat()
            if date not in item.dates:
                item.dates.append(date)
        item.proofs.append(Proof(source_id=str(edge.source_id), edge_id=str(edge.id)))

    for item in by_identity.values():
        item.specialties.sort()
        item.dates.sort()
    return list(by_identity.values())


def _evidence_doctors(graph, models, *, tenant_id: uuid.UUID, run_ids: set[uuid.UUID],
                      text_by_source: dict[uuid.UUID, str], skip_identities: set[str],
                      answer: DoctorsAnswer) -> tuple[list[DoctorItem], set[str]]:
    """Путь по доказательствам: личность → состав → упоминание → спан.

    Личность без состава сюда не попадает по построению: выборка идёт от
    строк состава, а не от личностей. Это и есть «zero-member identities
    никогда не участвуют в ответах» — свойством запроса, а не проверкой,
    которую можно забыть.

    `skip_identities` — личности, про которые уже ответил путь графа.
    Доказательство для них искать незачем: ребро сильнее, и повтор
    превратил бы одного человека в два пункта ответа.

    Возвращает пункты И множество ВСЕХ личностей-людей с составом,
    которые попались. Второе нужно, чтобы посчитать непокрытых: без
    него «трое врачей» не отличить от «трое из троих».
    """
    rows = graph.execute(
        select(models.identity, models.node)
        .join(models.member, models.member.identity_id == models.identity.id)
        .join(models.node, models.node.id == models.member.node_id)
        .where(models.identity.knowledge_user_id == tenant_id,
               models.identity.entity_type.in_(_PERSON_TYPES),
               models.node.kind == SemanticNodeKind.ENTITY,
               models.node.status == SemanticNodeStatus.ACTIVE,
               models.node.semantic_run_id.in_(run_ids))
        .order_by(models.identity.normalized_key, models.node.created_at)).all()

    known = {str(r[0].id) for r in rows}
    answer.count("личности-люди с составом", len(known))
    answer.count("узлы в их составе", len(rows))
    by_identity: dict[str, DoctorItem] = {}
    for identity, node in rows:
        if str(identity.id) in skip_identities:
            answer.skip("личность уже отвечена путём графа")
            continue
        for mention, span in _spans_for(graph, models, tenant_id=tenant_id,
                                        node_id=node.id,
                                        text_by_source=text_by_source, answer=answer):
            is_doctor, specialty = doctor_proof(span, node.canonical_label)
            if not is_doctor:
                answer.skip("в цитате нет врачебного маркера")
                # Замер, а не смягчение правила: ответ этот случай не
                # получает. Нужен, чтобы «ноль врачей» имело причину, а
                # не осталось числом. R5 стоил лишнего цикла ровно
                # потому, что прогон сказал «0» и не сказал почему.
                source_text_ = text_by_source.get(mention.source_id, "")
                if marker_precedes_span(source_text_, mention.char_start):
                    answer.skip("маркер не в цитате, но стоит перед спаном "
                                "в тексте источника")
                if marker_follows_label(span, node.canonical_label):
                    answer.skip("маркер стоит после подписи, а не перед ней")
                continue
            item = by_identity.get(str(identity.id))
            if item is None:
                item = DoctorItem(identity_id=str(identity.id),
                                  person=identity.canonical_label)
                by_identity[str(identity.id)] = item
            if specialty and specialty not in item.specialties:
                item.specialties.append(specialty)
            item.proofs.append(Proof(
                source_id=str(mention.source_id), window_id=mention.window_id,
                char_start=mention.char_start, char_end=mention.char_end, quote=span))
    for item in by_identity.values():
        item.specialties.sort()
    return list(by_identity.values()), known


def _answer_in(graph, models, *, tenant_id: uuid.UUID, run_ids: set[uuid.UUID],
               text_by_source: dict[uuid.UUID, str],
               answer: DoctorsAnswer) -> tuple[list[DoctorItem], str]:
    """Один ответ в одной схеме: граф, затем доказательства на остаток.

    Раньше выбирался ОДИН путь целиком: есть рёбра — отвечаем графом, нет
    — доказательствами. На пилотных восьми источниках разницы не было
    (рёбер ноль), а на широком корпусе она появилась: часть источников
    даёт рёбра, часть нет. Выбор одного пути тогда либо теряет
    доказанное, либо выдаёт неполный граф за полный ответ. §14.12
    требует ровно слияния: «merge only evidence-backed additions into the
    answer and label internally which part came from fallback».
    """
    if not run_ids:
        return [], AnswerPath.NONE

    identity_by_node = {
        node_id: identity_id for identity_id, node_id in graph.execute(
            select(models.member.identity_id, models.member.node_id)
            .where(models.member.knowledge_user_id == tenant_id)).all()}

    graph_items = _graph_doctors(graph, models, tenant_id=tenant_id, run_ids=run_ids,
                                 identity_by_node=identity_by_node,
                                 text_by_source=text_by_source, answer=answer)
    covered = {item.identity_id for item in graph_items if item.identity_id}
    evidence_items, known = _evidence_doctors(
        graph, models, tenant_id=tenant_id, run_ids=run_ids,
        text_by_source=text_by_source, skip_identities=covered, answer=answer)

    items = graph_items + evidence_items
    answered = {item.identity_id for item in items if item.identity_id}
    answer.uncovered_identities += len(known - answered)

    if graph_items and evidence_items:
        return items, AnswerPath.MIXED
    if graph_items:
        return items, AnswerPath.GRAPH
    if evidence_items:
        return items, AnswerPath.EVIDENCE
    return items, AnswerPath.EVIDENCE if known else AnswerPath.NONE


def answer_doctors_visited(session: Session, *, question: str,
                           knowledge_user_id: uuid.UUID | None = None) -> DoctorsAnswer:
    """Ответ на «каких врачей я посещал» по обеим схемам.

    Разделение то же, что у публикации и у разрешения сущностей:
    health-источник писал узлы в зеркало отдельной ролью, значит и
    ответ по ним собирается там же, своим соединением.
    """
    answer = DoctorsAnswer(question=question, intent=detect_intent(question))
    if answer.intent != QuestionIntent.DOCTORS_VISITED:
        return answer

    tenant_id = bind_knowledge_user(session, knowledge_user_id)
    sources = session.scalars(
        select(KnowledgeSource)
        .where(KnowledgeSource.current_semantic_run_id.is_not(None))).all()

    public_runs: set[uuid.UUID] = set()
    health_runs: set[uuid.UUID] = set()
    public_text = _SourceTexts(answer)
    health_text = _SourceTexts(answer)
    for source in sources:
        health = is_health_domain(source.domain)
        (health_runs if health else public_runs).add(source.current_semantic_run_id)
        (health_text if health else public_text).add(source)

    items, path = _answer_in(session, PUBLIC_MODELS, tenant_id=tenant_id,
                             run_ids=public_runs, text_by_source=public_text,
                             answer=answer)
    if health_runs and health_schema_configured():
        with health_session(tenant_id) as graph:
            health_items, health_path = _answer_in(
                graph, HEALTH_MODELS, tenant_id=tenant_id, run_ids=health_runs,
                text_by_source=health_text, answer=answer)
        items = items + health_items
        if path == AnswerPath.NONE:
            path = health_path
        elif health_path not in (AnswerPath.NONE, path):
            path = AnswerPath.MIXED

    answer.items = items
    answer.path_used = path
    return answer


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R7: ответ по доказанному")
    parser.add_argument("--question", required=True)
    parser.add_argument("--out", required=True,
                        help="файл для полного ответа (имена и цитаты)")
    args = parser.parse_args(argv)

    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    with sessionmaker(engine, expire_on_commit=False)() as session:
        answer = answer_doctors_visited(session, question=args.question)
        session.rollback()

    out = Path(args.out)
    out.write_text(json.dumps(answer.as_dict(), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    out.chmod(0o600)
    print(json.dumps(answer.as_public_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
