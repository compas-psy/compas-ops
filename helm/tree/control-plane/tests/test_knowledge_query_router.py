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
                 nodes=()):
        self.pairs = list(pairs)              # (identity, node)
        self.mentions = list(mentions)
        self.doctor_edges = list(doctor_edges)  # (edge, person_node)
        self.role_edges = list(role_edges)      # (person_id, concept_label)
        self.nodes = {n.id: n for n in nodes}

    def execute(self, query):
        first = query.column_descriptions[0]
        # Имя колонки проверяется РАНЬШЕ таблицы: у `select(edge.from_node_id,
        # ...)` в описании стоит та же таблица, что у `select(edge, ...)`,
        # и проверка по таблице увела бы запрос ролей в ветку рёбер.
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
            return _Result(self.mentions)
        raise AssertionError(f"неожиданный scalars() по {entity}")

    def get(self, _model, node_id):
        return self.nodes.get(node_id)


def _evidence(session, text, answer=None):
    answer = answer or qr.DoctorsAnswer(question="каких врачей я посещал?",
                                        intent=qr.QuestionIntent.DOCTORS_VISITED)
    items = qr._evidence_doctors(session, PUBLIC_MODELS, tenant_id=TENANT,
                                 run_ids={RUN}, text_by_source={SOURCE: text},
                                 answer=answer)
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
    assert items[0].occurred_at is None


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
    person = _Node("Иванов Пётр Сергеевич")
    event = _Node("приём уролога")
    concept = _Node("уролог")
    edge = _Edge(event.id, person.id, role="doctor")
    session = _FakeSession(doctor_edges=[(edge, person)],
                           role_edges=[(person.id, concept.canonical_label)],
                           nodes=[event])
    answer = qr.DoctorsAnswer(question="каких врачей я посещал?",
                              intent=qr.QuestionIntent.DOCTORS_VISITED)
    items, path = qr._answer_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                                run_ids={RUN}, text_by_source={}, answer=answer)
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
