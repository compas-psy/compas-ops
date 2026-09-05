"""R9 — производный граф знаний: чем он не является.

§30.8.5 J требует доказать: пути RepoGraphify и KnowledgeGraphify
различны, граф на пользователя, контуры изолированы, разметка реальная,
пересборка воспроизводима, удаление вывода ничего не разрушает.
Каждое проверяется здесь отдельно.

База не нужна: модуль читает строки и раскладывает файлы. Сессия
поддельная — и заодно доказывает, что модуль в базу не пишет.
"""

from __future__ import annotations

import json
import uuid

import pytest

from helm_core.knowledge import knowledge_graphify as kg
from helm_core.knowledge.semantic_publish import PUBLIC_MODELS
from helm_core.models.base import SemanticNodeKind, SemanticNodeStatus

TENANT = uuid.UUID("00000000-0000-0000-0000-00000000beef")
RUN = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
SRC_A = uuid.UUID("00000000-0000-0000-0000-00000000a001")
SRC_B = uuid.UUID("00000000-0000-0000-0000-00000000b002")


class _Node:
    def __init__(self, label, kind=SemanticNodeKind.ENTITY, *, entity_type="person",
                 subtype=None, statement_text=None):
        self.id = uuid.uuid4()
        self.knowledge_user_id = TENANT
        self.kind = kind
        self.subtype = subtype
        self.entity_type = entity_type
        self.canonical_label = label
        self.statement_text = statement_text
        self.occurred_at_start = None
        self.date_precision = None
        self.status = SemanticNodeStatus.ACTIVE
        self.semantic_run_id = RUN


class _Mention:
    def __init__(self, node_id, source_id, *, char_start=0, char_end=10, window_id=0):
        self.node_id = node_id
        self.source_id = source_id
        self.char_start = char_start
        self.char_end = char_end
        self.window_id = window_id
        self.semantic_run_id = RUN


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, *, nodes=(), mentions=(), edges=(), members=()):
        self.nodes = list(nodes)
        self.mentions = list(mentions)
        self.edges = list(edges)
        self.members = list(members)   # (identity_id, node_id)
        self.writes = 0

    def scalars(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is PUBLIC_MODELS.node:
            return _Result(sorted(self.nodes, key=lambda n: str(n.id)))
        if entity is PUBLIC_MODELS.mention:
            return _Result(self.mentions)
        if entity is PUBLIC_MODELS.edge:
            return _Result(self.edges)
        raise AssertionError(f"неожиданный scalars() по {entity}")

    def execute(self, _query):
        return _Result(self.members)

    def add(self, _obj):
        self.writes += 1

    def commit(self):
        self.writes += 1

    def flush(self):
        self.writes += 1


def _build(session, tmp_path, *, dry_run=False):
    return kg.build_scope(
        session, PUBLIC_MODELS, tenant_id=TENANT, scope="general", run_ids={RUN},
        root=tmp_path / "derived" / "graphify", markdown_root=tmp_path / "semantic",
        dry_run=dry_run)


# --- пути: отдельность и изоляция контуров ---------------------------------

def test_health_граф_лежит_в_приватном_дереве(monkeypatch):
    monkeypatch.setattr(kg, "health_schema_configured", lambda: True)
    monkeypatch.setattr(kg, "scope_root", lambda root, *, domain, knowledge_user_id: (
        f"{root}-private/health/users/{knowledge_user_id}"
        if domain == "health" else root))
    health = kg.graphify_root("/opt/helm-knowledge", domain="health",
                              knowledge_user_id=TENANT)
    general = kg.graphify_root("/opt/helm-knowledge", domain="general",
                               knowledge_user_id=TENANT)
    assert "-private/health/" in str(health)
    # Главное свойство §14.16: приватное дерево — отдельный КОРЕНЬ, а не
    # подкаталог общего. Обход общего Vault health-файлов не встретит.
    assert not str(health).startswith("/opt/helm-knowledge/")
    assert str(general).startswith("/opt/helm-knowledge/")


def test_разметка_health_тоже_вне_общего_дерева(monkeypatch):
    monkeypatch.setattr(kg, "scope_root", lambda root, *, domain, knowledge_user_id: (
        f"{root}-private/health/users/{knowledge_user_id}"
        if domain == "health" else root))
    path = kg.semantic_root("/opt/helm-knowledge", domain="health",
                            knowledge_user_id=TENANT)
    assert not str(path).startswith("/opt/helm-knowledge/")


def test_путь_не_совпадает_с_repograhify():
    # K11: `tools/graphify.py` и `graph/ops` — навигация по репозиторию,
    # и ни один путь пользовательского графа туда не ведёт.
    path = str(kg.graphify_root("/opt/helm-knowledge", domain="general",
                                knowledge_user_id=TENANT))
    assert "graph/ops" not in path
    assert "tools/graphify" not in path
    assert "derived/graphify" in path


def test_граф_разложен_по_пользователям():
    other = uuid.uuid4()
    mine = kg.graphify_root("/opt/helm-knowledge", domain="general",
                            knowledge_user_id=TENANT)
    theirs = kg.graphify_root("/opt/helm-knowledge", domain="general",
                              knowledge_user_id=other)
    assert mine != theirs
    assert str(TENANT) in str(mine)


# --- производность ---------------------------------------------------------

def test_состав_личности_даёт_производную_связь_а_не_ребро(tmp_path):
    # Рёбер нет вовсе, но один человек упомянут в двух документах —
    # путь «документ А → личность → документ Б» существует и доказан.
    one, two = _Node("Иванов Пётр Сергеевич"), _Node("Иванов Пётр Сергеевич")
    identity = uuid.uuid4()
    session = _FakeSession(
        nodes=[one, two],
        mentions=[_Mention(one.id, SRC_A), _Mention(two.id, SRC_B)],
        members=[(identity, one.id), (identity, two.id)])
    report = _build(session, tmp_path)

    assert report.edges_canonical == 0
    assert report.links_derived == 2          # в обе стороны
    assert report.cross_source_nodes == 2
    payload = json.loads((tmp_path / "derived" / "graphify" / "graph.json").read_text())
    assert payload["edges"] == []
    assert all(link["derived"] is True for link in payload["derived_links"])
    assert {link["type"] for link in payload["derived_links"]} == {kg.DERIVED_SAME_AS}


def test_один_источник_не_даёт_межисточниковой_связности(tmp_path):
    one, two = _Node("Иванов Пётр Сергеевич"), _Node("Иванов Пётр Сергеевич")
    identity = uuid.uuid4()
    session = _FakeSession(
        nodes=[one, two],
        mentions=[_Mention(one.id, SRC_A), _Mention(two.id, SRC_A)],
        members=[(identity, one.id), (identity, two.id)])
    report = _build(session, tmp_path)
    assert report.links_derived == 2
    assert report.cross_source_nodes == 0


def test_модуль_в_базу_не_пишет(tmp_path):
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(nodes=[node], mentions=[_Mention(node.id, SRC_A)])
    _build(session, tmp_path)
    assert session.writes == 0


# --- воспроизводимость -----------------------------------------------------

def test_пересборка_побайтово_совпадает(tmp_path):
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(nodes=[node], mentions=[_Mention(node.id, SRC_A)])
    _build(session, tmp_path)
    first = (tmp_path / "derived" / "graphify" / "graph.json").read_bytes()
    md_first = (tmp_path / "semantic" / "entity" / f"{node.id}.md").read_bytes()

    _build(session, tmp_path)
    assert (tmp_path / "derived" / "graphify" / "graph.json").read_bytes() == first
    assert (tmp_path / "semantic" / "entity" / f"{node.id}.md").read_bytes() == md_first


def test_удаление_вывода_ничего_не_теряет(tmp_path):
    import shutil
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(nodes=[node], mentions=[_Mention(node.id, SRC_A)])
    _build(session, tmp_path)
    before = (tmp_path / "derived" / "graphify" / "graph.json").read_bytes()

    shutil.rmtree(tmp_path / "derived")
    shutil.rmtree(tmp_path / "semantic")
    _build(session, tmp_path)
    assert (tmp_path / "derived" / "graphify" / "graph.json").read_bytes() == before


def test_сухой_прогон_не_пишет_на_диск(tmp_path):
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(nodes=[node], mentions=[_Mention(node.id, SRC_A)])
    report = _build(session, tmp_path, dry_run=True)
    assert report.nodes == 1
    assert report.markdown_written == 0
    assert not (tmp_path / "derived").exists()
    assert not (tmp_path / "semantic").exists()


# --- разметка --------------------------------------------------------------

def test_разметка_несёт_провенанс_и_устойчивую_ссылку(tmp_path):
    person = _Node("Иванов Пётр Сергеевич")
    event = _Node("Приём уролога", kind=SemanticNodeKind.EVENT, entity_type=None,
                  subtype="medical_visit", statement_text="Приём у Иванова.")
    edge = type("E", (), {"id": uuid.uuid4(), "from_node_id": event.id,
                          "to_node_id": person.id, "relation_type": "involves",
                          "role": "doctor", "source_id": SRC_A,
                          "semantic_run_id": RUN})()
    session = _FakeSession(
        nodes=[person, event],
        mentions=[_Mention(person.id, SRC_A, char_start=12, char_end=33),
                  _Mention(event.id, SRC_A, char_start=0, char_end=40)],
        edges=[edge])
    _build(session, tmp_path)

    text = (tmp_path / "semantic" / "event" / f"{event.id}.md").read_text()
    assert f"source: {SRC_A}" in text
    assert "span: 0-40" in text
    assert f"[[entity-{person.id}|Иванов Пётр Сергеевич]]" in text
    assert "involves (роль: doctor)" in text
    assert "Приём у Иванова." in text


def test_права_файлов_дают_группе_чтение_и_не_дают_прочим(tmp_path):
    # 0640, не 0600: приватное дерево обязано оставаться читаемой
    # .md-структурой для Obsidian владельца (SPEC_DEVIATION D-1), а
    # setgid-каталог отдаёт файлу группу `helm-health`. И не 0644:
    # посторонним здесь читать нечего.
    node = _Node("Иванов Пётр Сергеевич")
    session = _FakeSession(nodes=[node], mentions=[_Mention(node.id, SRC_A)])
    _build(session, tmp_path)
    for path in ((tmp_path / "derived" / "graphify" / "graph.json"),
                 (tmp_path / "semantic" / "entity" / f"{node.id}.md")):
        assert oct(path.stat().st_mode)[-3:] == "640"


def test_пустой_контур_не_создаёт_каталогов(tmp_path):
    report = kg.build_scope(_FakeSession(), PUBLIC_MODELS, tenant_id=TENANT,
                            scope="general", run_ids=set(),
                            root=tmp_path / "derived", markdown_root=tmp_path / "semantic",
                            dry_run=False)
    assert report.nodes == 0
    assert not (tmp_path / "derived").exists()
