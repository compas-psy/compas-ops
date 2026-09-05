"""R7 — ответ только из доказанного: что попадает в ответ, а что нет.

Распоряжение владельца 05.09.2026: «Zero-member identities никогда не
участвуют в ответах... Если ФИО есть, а специальность не доказана: ФИО —
специальность не подтверждена... Никаких inference-догадок по названию
клиники, отделению или контексту.» Каждый из этих запретов проверяется
здесь отдельно: запрет без теста держится на памяти следующего правщика.

База не нужна — исполнитель читает строки и решает по тексту, поэтому
сессия поддельная, а правила настоящие.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from helm_core.knowledge import query_router as qr
from helm_core.knowledge.semantic_publish import PUBLIC_MODELS

TENANT = uuid.UUID("00000000-0000-0000-0000-00000000beef")
RUN = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
SOURCE = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


class _Node:
    def __init__(self, label, *, created_at=0):
        self.id = uuid.uuid4()
        self.canonical_label = label
        self.created_at = created_at
        self.occurred_at_start = None


class _Identity:
    def __init__(self, label):
        self.id = uuid.uuid4()
        self.canonical_label = label
        self.normalized_key = label.lower()


class _Mention:
    def __init__(self, node_id, char_start, char_end, *, window_id=0, source_id=SOURCE):
        self.node_id = node_id
        self.source_id = source_id
        self.window_id = window_id
        self.char_start = char_start
        self.char_end = char_end


class _Edge:
    def __init__(self, from_node_id, to_node_id, *, role=None):
        self.id = uuid.uuid4()
        self.from_node_id = from_node_id
        self.to_node_id = to_node_id
        self.role = role
        self.source_id = SOURCE


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _FakeSession:
    """Различает запросы по таблице/колонке, а не по тексту SQL."""

    def __init__(self, *, pairs=(), mentions=(), doctor_edges=(), role_edges=(),
                 nodes=(), members=()):
        self.pairs = list(pairs)              # (identity, node)
        self.mentions = list(mentions)
        self.doctor_edges = list(doctor_edges)  # (edge, person_node)
        self.role_edges = list(role_edges)      # (person_id, concept_label)
        self.nodes = {n.id: n for n in nodes}
        #: (identity_id, node_id) — состав личностей. Пусто по
        #: умолчанию: большинство тестов про одну схему без слияния.
        self.members = list(members)

    def execute(self, query):
        first = query.column_descriptions[0]
        # Имя колонки проверяется РАНЬШЕ таблицы: у `select(edge.from_node_id,
        # ...)` в описании стоит та же таблица, что у `select(edge, ...)`,
        # и проверка по таблице увела бы запрос ролей в ветку рёбер.
        if first["name"] == "identity_id":
            return _Result(self.members)
        if first["name"] == "from_node_id":
            return _Result(self.role_edges)
        if first.get("entity") is PUBLIC_MODELS.identity:
            return _Result(self.pairs)
        if first.get("entity") is PUBLIC_MODELS.edge:
            return _Result(self.doctor_edges)
        raise AssertionError(f"неожиданный execute() по {first}")

    def scalars(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is PUBLIC_MODELS.mention:
            # Упоминания фильтруются по узлу так же, как в проде: иначе
            # тест «специальность доказана вторым узлом» проходил бы по
            # чужим цитатам и ничего не проверял. Узел берётся из
            # bind-параметров скомпилированного запроса — устойчивее,
            # чем разбирать дерево условий руками.
            params = query.compile().params
            wanted = next((v for k, v in params.items() if k.startswith("node_id")),
                          None)
            return _Result([m for m in self.mentions
                            if wanted is None or m.node_id == wanted])
        raise AssertionError(f"неожиданный scalars() по {entity}")

    def get(self, _model, node_id):
        return self.nodes.get(node_id)


def _evidence(session, text, answer=None, skip=()):
    answer = answer or qr.DoctorsAnswer(question="каких врачей я посещал?",
                                        intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, _known = qr._evidence_doctors(
        session, PUBLIC_MODELS, tenant_id=TENANT, run_ids={RUN},
        text_by_source={SOURCE: text}, skip_identities=set(skip), answer=answer)
    return items, answer


# --- распознавание вопроса ------------------------------------------------

def test_вопрос_о_врачах_распознан():
    assert qr.detect_intent("каких врачей я посещал?") == qr.QuestionIntent.DOCTORS_VISITED


def test_врач_без_посещения_не_этот_вопрос():
    # «что сказал врач про давление» — другой вопрос, и отвечать на него
    # этим исполнителем нельзя.
    assert qr.detect_intent("что сказал врач про давление") == qr.QuestionIntent.UNSUPPORTED


def test_посещение_без_врача_не_этот_вопрос():
    assert qr.detect_intent("где я был в марте") == qr.QuestionIntent.UNSUPPORTED


# --- что считается доказательством врачебности ----------------------------

def test_дефисный_маркер_доказывает_специальность():
    assert qr.doctor_proof("заключение: врач-уролог Иванов Пётр Сергеевич",
                           "Иванов Пётр Сергеевич") == (True, "уролог")


def test_маркер_без_дефиса_доказывает_врача_но_не_специальность():
    assert qr.doctor_proof("осмотр провёл врач Иванов Пётр Сергеевич",
                           "Иванов Пётр Сергеевич") == (True, None)


def test_без_маркера_не_врач():
    assert qr.doctor_proof("Иванов Пётр Сергеевич пришёл на приём",
                           "Иванов Пётр Сергеевич") == (False, None)


def test_отделение_не_доказывает_врача():
    # Прямой запрет владельца: никаких догадок по отделению и клинике.
    assert qr.doctor_proof("Иванов Пётр Сергеевич, отделение урологии",
                           "Иванов Пётр Сергеевич") == (False, None)


def test_маркер_дальше_трёх_токенов_не_считается():
    assert qr.doctor_proof(
        "врач направил на анализы и выдал заключение Иванов Пётр Сергеевич",
        "Иванов Пётр Сергеевич") == (False, None)


def test_специальность_из_другого_места_не_приписывается():
    # «уролог» есть в тексте, но не в дефисной связке перед подписью.
    assert qr.doctor_proof("уролог вёл приём, заключение подписал врач Иванов Пётр",
                           "Иванов Пётр") == (True, None)


# --- формулировка ответа ---------------------------------------------------

def test_недоказанная_специальность_названа_дословно():
    item = qr.DoctorItem(identity_id="x", person="Иванов Пётр Сергеевич")
    assert item.line() == "Иванов Пётр Сергеевич — специальность не подтверждена"


def test_доказанная_специальность_в_строке():
    item = qr.DoctorItem(identity_id="x", person="Иванов Пётр Сергеевич",
                         specialties=["уролог"])
    assert item.line() == "Иванов Пётр Сергеевич — уролог"


# --- путь по доказательствам ----------------------------------------------

def test_личность_с_составом_и_спаном_попадает_в_ответ():
    text = "Приём вёл врач-уролог Иванов Пётр Сергеевич, заключение выдано."
    node = _Node("Иванов Пётр Сергеевич")
    identity = _Identity("Иванов Пётр Сергеевич")
    session = _FakeSession(pairs=[(identity, node)],
                           mentions=[_Mention(node.id, 10, 46)])
    items, answer = _evidence(session, text)
    assert len(items) == 1
    assert items[0].specialties == ["уролог"]
    assert items[0].proofs[0].char_start == 10
    assert answer.skipped == {}


def test_личность_без_состава_в_ответ_не_попадает():
    # Личность есть, строк состава нет — выборка идёт ОТ состава, значит
    # такая личность структурно недостижима.
    session = _FakeSession(pairs=[], mentions=[])
    items, _ = _evidence(session, "врач-уролог Иванов Пётр Сергеевич")
    assert items == []


def test_упоминание_без_точного_спана_не_доказательство():
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(pairs=[(_Identity("Иванов Пётр Сергеевич"), node)],
                           mentions=[])
    items, answer = _evidence(session, "врач-уролог Иванов Пётр Сергеевич")
    assert items == []
    assert answer.skipped["узел без точного спана"] == 1


def test_цитата_без_маркера_не_даёт_врача():
    text = "Пациента сопровождал Иванов Пётр Сергеевич."
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(pairs=[(_Identity("Иванов Пётр Сергеевич"), node)],
                           mentions=[_Mention(node.id, 0, len(text))])
    items, answer = _evidence(session, text)
    assert items == []
    assert answer.skipped["в цитате нет врачебного маркера"] == 1


def test_два_упоминания_одной_личности_дают_один_ответ_и_два_доказательства():
    text = ("врач-уролог Иванов Пётр Сергеевич принял. "
            "Повторно: врач Иванов Пётр Сергеевич.")
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(
        pairs=[(_Identity("Иванов Пётр Сергеевич"), node)],
        mentions=[_Mention(node.id, 0, 34), _Mention(node.id, 46, 76)])
    items, _ = _evidence(session, text)
    assert len(items) == 1
    assert len(items[0].proofs) == 2
    assert items[0].specialties == ["уролог"]


def test_дата_на_пути_доказательств_не_придумывается():
    text = "врач-уролог Иванов Пётр Сергеевич, 12.03.2026"
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(pairs=[(_Identity("Иванов Пётр Сергеевич"), node)],
                           mentions=[_Mention(node.id, 0, 33)])
    items, _ = _evidence(session, text)
    assert items[0].dates == []


# --- путь по графу ---------------------------------------------------------

def test_пустой_граф_переключает_на_доказательства():
    text = "врач-уролог Иванов Пётр Сергеевич"
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(pairs=[(_Identity("Иванов Пётр Сергеевич"), node)],
                           mentions=[_Mention(node.id, 0, len(text))],
                           doctor_edges=[])
    answer = qr.DoctorsAnswer(question="каких врачей я посещал?",
                              intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, path = qr._answer_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                                run_ids={RUN}, text_by_source={SOURCE: text},
                                answer=answer)
    assert path == qr.AnswerPath.EVIDENCE
    assert len(items) == 1
    assert answer.graph_edges == 0


def test_доказанное_ребро_отвечает_графом():
    text = "Приём вёл врач-уролог Иванов Пётр Сергеевич 19.08.2026."
    person = _Node("Иванов Пётр Сергеевич")
    event = _Node("приём уролога")
    concept = _Node("уролог")
    identity = _Identity("Иванов Пётр Сергеевич")
    edge = _Edge(event.id, person.id, role="doctor")
    session = _FakeSession(doctor_edges=[(edge, person)],
                           role_edges=[(person.id, concept.canonical_label)],
                           nodes=[event], members=[(identity.id, person.id)],
                           mentions=[_Mention(person.id, 10, len(text) - 1)])
    answer = qr.DoctorsAnswer(question="каких врачей я посещал?",
                              intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, path = qr._answer_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                                run_ids={RUN}, text_by_source={SOURCE: text},
                                answer=answer)
    assert path == qr.AnswerPath.GRAPH
    assert items[0].specialties == ["уролог"]
    assert items[0].proofs[0].edge_id == str(edge.id)


# --- что уходит наружу -----------------------------------------------------

def test_в_публичной_сводке_нет_ни_имён_ни_цитат():
    # §5.2: имя врача из выписки — медицинская информация, в лог Actions
    # она не уходит.
    text = "врач-уролог Иванов Пётр Сергеевич"
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(pairs=[(_Identity("Иванов Пётр Сергеевич"), node)],
                           mentions=[_Mention(node.id, 0, len(text))])
    items, answer = _evidence(session, text)
    answer.items = items
    public = json.dumps(answer.as_public_dict(), ensure_ascii=False)
    assert "Иванов" not in public
    assert "уролог" not in public
    assert json.loads(public)["items_with_specialty"] == 1


# --- диагностика «почему ноль» --------------------------------------------

def test_маркер_перед_спаном_считается_но_в_ответ_не_попадает():
    # Цитата извлекателя началась с фамилии, «врач-уролог» остался за
    # её левой границей. Ответ этот случай не получает — правило
    # доказательства не смягчается, — но причина нуля названа числом.
    text = "Приём вёл врач-уролог Иванов Пётр Сергеевич."
    start = text.index("Иванов")
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(pairs=[(_Identity("Иванов Пётр Сергеевич"), node)],
                           mentions=[_Mention(node.id, start, len(text) - 1)])
    items, answer = _evidence(session, text)
    assert items == []
    assert answer.skipped["в цитате нет врачебного маркера"] == 1
    assert answer.skipped["маркер не в цитате, но стоит перед спаном "
                          "в тексте источника"] == 1


def test_без_маркера_нигде_диагностика_молчит():
    text = "Пациента сопровождал Иванов Пётр Сергеевич."
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(pairs=[(_Identity("Иванов Пётр Сергеевич"), node)],
                           mentions=[_Mention(node.id, 21, len(text))])
    items, answer = _evidence(session, text)
    assert items == []
    assert "маркер не в цитате, но стоит перед спаном в тексте источника" \
        not in answer.skipped


def test_маркер_после_подписи_считается_но_в_ответ_не_попадает():
    # «Иванов И.И., врач-уролог» — обычная подпись под выпиской.
    # Аттестованное правило смотрит только назад, поэтому ответа нет,
    # но потеря названа числом.
    text = "Заключение подписал Иванов Пётр Сергеевич, врач-уролог."
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(pairs=[(_Identity("Иванов Пётр Сергеевич"), node)],
                           mentions=[_Mention(node.id, 20, len(text))])
    items, answer = _evidence(session, text)
    assert items == []
    assert answer.skipped["маркер стоит после подписи, а не перед ней"] == 1


def test_рассмотренное_считается():
    text = "врач-уролог Иванов Пётр Сергеевич"
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(pairs=[(_Identity("Иванов Пётр Сергеевич"), node)],
                           mentions=[_Mention(node.id, 0, len(text))])
    _, answer = _evidence(session, text)
    assert answer.considered == {"личности-люди с составом": 1,
                                 "узлы в их составе": 1,
                                 "упоминания с точным спаном": 1}


# --- слияние путей (§14.12) ------------------------------------------------

def test_граф_и_доказательства_мержатся_а_не_вытесняют_друг_друга():
    # На широком корпусе часть источников даёт рёбра, часть нет. Выбор
    # ОДНОГО пути тогда либо теряет доказанное, либо выдаёт неполный
    # граф за полный ответ.
    text = "врач-уролог Петрова Анна Ивановна"
    by_graph = _Node("Сидоров Иван Петрович")
    event = _Node("приём")
    by_evidence = _Node("Петрова Анна Ивановна")
    edge = _Edge(event.id, by_graph.id, role="doctor")
    graph_identity, evidence_identity = _Identity("Сидоров"), _Identity("Петрова")
    session = _FakeSession(
        doctor_edges=[(edge, by_graph)], nodes=[event],
        members=[(graph_identity.id, by_graph.id),
                 (evidence_identity.id, by_evidence.id)],
        pairs=[(evidence_identity, by_evidence)],
        mentions=[_Mention(by_evidence.id, 0, len(text))])
    answer = qr.DoctorsAnswer(question="каких врачей я посещал?",
                              intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, path = qr._answer_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                                run_ids={RUN}, text_by_source={SOURCE: text},
                                answer=answer)
    answer.items = items
    assert path == qr.AnswerPath.MIXED
    assert answer.by_path(qr.AnswerPath.GRAPH) == 1
    assert answer.by_path(qr.AnswerPath.EVIDENCE) == 1
    assert answer.uncovered_identities == 0


def test_личность_отвеченная_графом_не_повторяется_доказательством():
    # Иначе один человек стал бы двумя пунктами ответа.
    text = "врач-уролог Сидоров Иван Петрович"
    person = _Node("Сидоров Иван Петрович")
    event = _Node("приём")
    identity = _Identity("Сидоров Иван Петрович")
    edge = _Edge(event.id, person.id, role="doctor")
    session = _FakeSession(
        doctor_edges=[(edge, person)], nodes=[event],
        members=[(identity.id, person.id)],
        pairs=[(identity, person)],
        mentions=[_Mention(person.id, 0, len(text))])
    answer = qr.DoctorsAnswer(question="каких врачей я посещал?",
                              intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, path = qr._answer_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                                run_ids={RUN}, text_by_source={SOURCE: text},
                                answer=answer)
    assert len(items) == 1
    assert path == qr.AnswerPath.GRAPH
    assert answer.skipped["личность уже отвечена путём графа"] == 1


def test_непокрытые_личности_считаются():
    # Человек в документах есть, доказательства врачебной роли нет.
    # «Один врач» без этого числа читается как «в документах один
    # человек».
    text = "Пациента сопровождал Кузнецов Олег Иванович."
    node = _Node("Кузнецов Олег Иванович")
    identity = _Identity("Кузнецов Олег Иванович")
    session = _FakeSession(pairs=[(identity, node)],
                           members=[(identity.id, node.id)],
                           mentions=[_Mention(node.id, 0, len(text))])
    answer = qr.DoctorsAnswer(question="каких врачей я посещал?",
                              intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, _ = qr._answer_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                             run_ids={RUN}, text_by_source={SOURCE: text},
                             answer=answer)
    assert items == []
    assert answer.uncovered_identities == 1


def test_публичная_сводка_несёт_разбивку_по_путям_и_остаётся_без_имён():
    text = "врач-уролог Петрова Анна Ивановна"
    node = _Node("Петрова Анна Ивановна")
    identity = _Identity("Петрова Анна Ивановна")
    session = _FakeSession(pairs=[(identity, node)],
                           members=[(identity.id, node.id)],
                           mentions=[_Mention(node.id, 0, len(text))])
    answer = qr.DoctorsAnswer(question="каких врачей я посещал?",
                              intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, answer.path_used = qr._answer_in(
        session, PUBLIC_MODELS, tenant_id=TENANT, run_ids={RUN},
        text_by_source={SOURCE: text}, answer=answer)
    answer.items = items
    public = json.dumps(answer.as_public_dict(), ensure_ascii=False)
    assert json.loads(public)["items_from_evidence"] == 1
    assert json.loads(public)["items_from_graph"] == 0
    assert json.loads(public)["uncovered_identities"] == 0
    assert "Петрова" not in public and "уролог" not in public


# --- канонизация внутри пути графа -----------------------------------------

def test_два_визита_к_одному_врачу_это_один_врач():
    # Иначе `items_from_graph` считал бы посещения, а не врачей.
    text = "Приём вёл врач-уролог Иванов Пётр Сергеевич."
    person = _Node("Иванов Пётр Сергеевич")
    first, second = _Node("приём 1"), _Node("приём 2")
    first.occurred_at_start = datetime(2026, 3, 14, tzinfo=timezone.utc)
    second.occurred_at_start = datetime(2026, 5, 20, tzinfo=timezone.utc)
    identity = _Identity("Иванов Пётр Сергеевич")
    session = _FakeSession(
        doctor_edges=[(_Edge(first.id, person.id, role="doctor"), person),
                      (_Edge(second.id, person.id, role="doctor"), person)],
        nodes=[first, second], members=[(identity.id, person.id)],
        mentions=[_Mention(person.id, 10, len(text) - 1)])
    answer = qr.DoctorsAnswer(question="каких врачей я посещал?",
                              intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, _ = qr._answer_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                             run_ids={RUN}, text_by_source={SOURCE: text},
                             answer=answer)
    assert len(items) == 1
    assert len(items[0].proofs) == 2
    assert items[0].dates == ["2026-03-14", "2026-05-20"]
    assert answer.graph_edges == 2


def test_узел_графа_без_канонической_личности_врачом_не_считается():
    # Личности у него нет не случайно: R6 отказался её назначить.
    # Пропустить такой узел значило бы обойти решение R6 сбоку.
    text = "врач-уролог Иванов"
    person = _Node("Иванов")
    event = _Node("приём")
    session = _FakeSession(doctor_edges=[(_Edge(event.id, person.id, role="doctor"),
                                          person)],
                           nodes=[event], members=[],
                           mentions=[_Mention(person.id, 0, len(text))])
    answer = qr.DoctorsAnswer(question="каких врачей я посещал?",
                              intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, path = qr._answer_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                                run_ids={RUN}, text_by_source={SOURCE: text},
                                answer=answer)
    assert items == []
    assert path == qr.AnswerPath.NONE
    assert answer.skipped["узел графа без канонической личности"] == 1


def test_число_пунктов_равно_объединению_канонических_личностей():
    # Инвариант: len(items) = |graph_ids ∪ evidence_ids|.
    text = "врач-уролог Петрова Анна Ивановна"
    graph_person = _Node("Сидоров Иван Петрович")
    event = _Node("приём")
    shared = _Node("Сидоров Иван Петрович")     # тот же человек, другой узел
    only_evidence = _Node("Петрова Анна Ивановна")
    graph_identity = _Identity("Сидоров Иван Петрович")
    evidence_identity = _Identity("Петрова Анна Ивановна")
    session = _FakeSession(
        doctor_edges=[(_Edge(event.id, graph_person.id, role="doctor"), graph_person)],
        nodes=[event],
        members=[(graph_identity.id, graph_person.id),
                 (graph_identity.id, shared.id),
                 (evidence_identity.id, only_evidence.id)],
        pairs=[(graph_identity, shared), (evidence_identity, only_evidence)],
        mentions=[_Mention(only_evidence.id, 0, len(text))])
    answer = qr.DoctorsAnswer(question="каких врачей я посещал?",
                              intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, _ = qr._answer_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                             run_ids={RUN}, text_by_source={SOURCE: text},
                             answer=answer)
    identities = {item.identity_id for item in items}
    assert len(items) == len(identities) == 2
    assert "" not in identities


# --- фильтр специальности (§14.9 доменно-агностичен) -----------------------

def test_немедицинская_роль_не_становится_специальностью():
    # На смешанном корпусе врач легко окажется ещё и руководителем
    # проекта. `HAS_ROLE` про обе роли говорит одинаково.
    text = "Приём вёл врач-уролог Иванов Пётр Сергеевич."
    person = _Node("Иванов Пётр Сергеевич")
    event = _Node("приём")
    identity = _Identity("Иванов Пётр Сергеевич")
    session = _FakeSession(
        doctor_edges=[(_Edge(event.id, person.id, role="doctor"), person)],
        role_edges=[(person.id, "уролог"), (person.id, "руководитель проекта")],
        nodes=[event], members=[(identity.id, person.id)],
        mentions=[_Mention(person.id, 10, len(text) - 1)])
    answer = qr.DoctorsAnswer(question="каких врачей я посещал?",
                              intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, _ = qr._answer_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                             run_ids={RUN}, text_by_source={SOURCE: text},
                             answer=answer)
    assert items[0].specialties == ["уролог"]
    assert answer.skipped[
        "роль из графа не подтверждена как медицинская специальность"] == 1


def test_только_немедицинская_роль_оставляет_специальность_недоказанной():
    text = "Совещание провёл врач Иванов Пётр Сергеевич."
    person = _Node("Иванов Пётр Сергеевич")
    event = _Node("совещание")
    identity = _Identity("Иванов Пётр Сергеевич")
    session = _FakeSession(
        doctor_edges=[(_Edge(event.id, person.id, role="doctor"), person)],
        role_edges=[(person.id, "руководитель проекта")],
        nodes=[event], members=[(identity.id, person.id)],
        mentions=[_Mention(person.id, 17, len(text) - 1)])
    answer = qr.DoctorsAnswer(question="каких врачей я посещал?",
                              intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, _ = qr._answer_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                             run_ids={RUN}, text_by_source={SOURCE: text},
                             answer=answer)
    assert items[0].specialties == []
    assert items[0].line().endswith("специальность не подтверждена")


def test_специальность_собирается_со_всех_узлов_личности():
    # Один и тот же врач приходит двумя узлами из разных документов, и
    # специальность доказана только во втором. Группировка по личности
    # не должна означать «смотрим только первый узел».
    text_a = "Пациента принял Иванов Пётр Сергеевич."
    text_b = "Заключение: врач-уролог Иванов Пётр Сергеевич."
    source_b = uuid.UUID("00000000-0000-0000-0000-0000000000cc")
    node_a, node_b = _Node("Иванов Пётр Сергеевич"), _Node("Иванов Пётр Сергеевич")
    event_a, event_b = _Node("приём A"), _Node("приём B")
    identity = _Identity("Иванов Пётр Сергеевич")
    mention_b = _Mention(node_b.id, 11, len(text_b) - 1)
    mention_b.source_id = source_b
    session = _FakeSession(
        doctor_edges=[(_Edge(event_a.id, node_a.id, role="doctor"), node_a),
                      (_Edge(event_b.id, node_b.id, role="doctor"), node_b)],
        nodes=[event_a, event_b],
        members=[(identity.id, node_a.id), (identity.id, node_b.id)],
        mentions=[_Mention(node_a.id, 16, len(text_a) - 1), mention_b])
    answer = qr.DoctorsAnswer(question="каких врачей я посещал?",
                              intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, _ = qr._answer_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                             run_ids={RUN},
                             text_by_source={SOURCE: text_a, source_b: text_b},
                             answer=answer)
    assert len(items) == 1
    assert len(items[0].proofs) == 2
    assert items[0].specialties == ["уролог"]
