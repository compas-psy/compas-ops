"""R6 — разрешение сущностей: что сливается, что нет и почему.

Распоряжение владельца 05.09.2026: «Auto-resolution только по strong
identity... Fuzzy/surname-only merge запрещён. Исходные
nodes/mentions/provenance не мутировать и не удалять.» Каждое из этих
слов проверяется здесь отдельным тестом — запрет, не подтверждённый
тестом, держится на памяти следующего правщика.

База не нужна: проход берёт строки и возвращает решение, поэтому
сессия здесь поддельная, а правила — настоящие.
"""

from __future__ import annotations

import uuid

from helm_core.knowledge import entity_resolution as er
from helm_core.knowledge.semantic_publish import PUBLIC_MODELS, normalize_key
from helm_core.models.base import (
    EntityIdentityMatch, EntityResolutionReason, SemanticNodeKind, SemanticNodeStatus,
)

TENANT = uuid.UUID("00000000-0000-0000-0000-00000000beef")
RUN = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


class _Node:
    """Ровно те поля узла, которые читает проход."""

    def __init__(self, label, entity_type="person", *, created_at=0, aliases=()):
        self.id = uuid.uuid4()
        self.knowledge_user_id = TENANT
        self.kind = SemanticNodeKind.ENTITY
        self.status = SemanticNodeStatus.ACTIVE
        self.entity_type = entity_type
        self.canonical_label = label
        # Той же функцией, что и запись: нормализуй тест иначе —
        # и он проверял бы не тот ключ, по которому сливает продакшн.
        self.normalized_key = normalize_key(label)
        self.semantic_run_id = RUN
        self.created_at = created_at
        self.aliases = tuple(aliases)


class _FakeSession:
    """Отдаёт узлы и уже записанные строки; собирает добавленное.

    Запросы различаются по таблице, а не по тексту SQL: тест обязан
    ломаться от изменения ПРАВИЛА, а не от переписанного запроса.
    """

    def __init__(self, nodes, *, members=(), identities=(), candidates=()):
        self.nodes = list(nodes)
        self.members = list(members)
        self.identities = list(identities)
        self.candidates = list(candidates)
        self.added = []
        self.flushed = False

    def _entity(self, query):
        return query.column_descriptions[0]["entity"]

    def scalars(self, query):
        entity = self._entity(query)
        if entity is PUBLIC_MODELS.node:
            return _Result(self.nodes)
        if entity is PUBLIC_MODELS.identity:
            return _Result(self.identities)
        if entity is PUBLIC_MODELS.member:      # select(member.node_id)
            return _Result([m.node_id for m in self.members])
        raise AssertionError(f"неожиданный scalars() по {entity}")

    def execute(self, query):
        first = query.column_descriptions[0]["name"]
        if first == "normalized_alias":
            by_node = {m.node_id: m.identity_id for m in self.members}
            return _Result([(alias, by_node[n.id])
                            for n in self.nodes if n.id in by_node
                            for alias in n.aliases])
        if first == "node_id":
            return _Result([(c.node_id, c.identity_id, c.reason) for c in self.candidates])
        raise AssertionError(f"неожиданный execute() по {first}")

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flushed = True


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def _run(session, **kw):
    return er.resolve_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                         current_run_ids={RUN}, **kw)


def _of(session, model):
    return [r for r in session.added if isinstance(r, model)]


class TestStrongIdentity:
    def test_same_normalized_label_and_type_become_one_identity(self):
        """Дословное совпадение подписи при том же типе — единственный
        случай, когда §14.7 разрешает слить без человека."""
        session = _FakeSession([_Node("Гаврилова Марина Сергеевна", created_at=1),
                                _Node("гаврилова  марина сергеевна", created_at=2)])
        report = _run(session)
        assert report.identities_created == 1
        assert report.members_created == 2
        assert report.matched_by == {EntityIdentityMatch.NORMALIZED_LABEL: 2}
        members = _of(session, PUBLIC_MODELS.member)
        assert len({m.identity_id for m in members}) == 1

    def test_same_label_different_type_stays_two_identities(self):
        """«Сеченов»-человек и «Сеченов»-организация: совпадение
        написания здесь довод ПРОТИВ слияния, а не за."""
        session = _FakeSession([_Node("Сеченов", "person", created_at=1),
                                _Node("Сеченов", "organization", created_at=2)])
        report = _run(session)
        assert report.identities_created == 2
        assert report.candidates_by_reason == {EntityResolutionReason.TYPE_CONFLICT: 1}

    def test_confirmed_alias_joins_the_same_identity(self):
        """Подтверждённый алиас — доказательство; догадка о написании —
        нет. Поэтому путь один: алиас, уже записанный за узлом
        личности."""
        first = _Node("Безручко Дарья Юрьевна", created_at=1,
                      aliases=("безручко д.ю.",))
        second = _Node("Безручко Д.Ю.", created_at=2)
        identity = PUBLIC_MODELS.identity(
            id=uuid.uuid4(), knowledge_user_id=TENANT, entity_type="person",
            canonical_label=first.canonical_label, normalized_key=first.normalized_key)
        member = PUBLIC_MODELS.member(
            id=uuid.uuid4(), knowledge_user_id=TENANT, identity_id=identity.id,
            node_id=first.id, matched_on=EntityIdentityMatch.NORMALIZED_LABEL)
        session = _FakeSession([first, second], members=[member], identities=[identity])
        report = _run(session)
        assert report.already_resolved == 1
        assert report.identities_created == 0
        assert report.matched_by == {EntityIdentityMatch.ALIAS: 1}
        assert _of(session, PUBLIC_MODELS.member)[0].identity_id == identity.id


class TestForbiddenMerges:
    def test_surname_only_is_a_candidate_never_a_merge(self):
        """Прямой запрет владельца. Однофамильцы существуют, а цена
        ошибки — чужая медицинская запись в карточке человека."""
        session = _FakeSession([_Node("Иванов Пётр Сергеевич", created_at=1),
                                _Node("Иванов", created_at=2)])
        report = _run(session)
        assert report.identities_created == 2, "фамилия без имени слита — это запрещено"
        assert report.candidates_by_reason == {EntityResolutionReason.SURNAME_ONLY: 1}
        assert len(_of(session, PUBLIC_MODELS.candidate)) == 1

    def test_different_people_produce_neither_merge_nor_candidate(self):
        """Кандидат — не «на всякий случай». Вопрос, заданный без
        основания, обесценивает список вопросов."""
        session = _FakeSession([_Node("Иванов Пётр", created_at=1),
                                _Node("Петров Иван", created_at=2)])
        report = _run(session)
        assert report.identities_created == 2
        assert report.candidates_created == 0

    def test_alias_of_another_type_is_not_proof(self):
        """Алиас доказывает тождество внутри типа. Организация с тем же
        написанием не становится человеком."""
        first = _Node("Сеченов", "organization", created_at=1, aliases=("сеченов и.м.",))
        second = _Node("Сеченов И.М.", "person", created_at=2)
        identity = PUBLIC_MODELS.identity(
            id=uuid.uuid4(), knowledge_user_id=TENANT, entity_type="organization",
            canonical_label=first.canonical_label, normalized_key=first.normalized_key)
        member = PUBLIC_MODELS.member(
            id=uuid.uuid4(), knowledge_user_id=TENANT, identity_id=identity.id,
            node_id=first.id, matched_on=EntityIdentityMatch.NORMALIZED_LABEL)
        session = _FakeSession([first, second], members=[member], identities=[identity])
        report = _run(session)
        assert report.matched_by == {EntityIdentityMatch.NORMALIZED_LABEL: 1}
        assert report.identities_created == 1


class TestSourcesAreNotTouched:
    """«Исходные nodes/mentions/provenance не мутировать и не удалять.»
    Проверяется структурно: проход добавляет строки трёх новых таблиц и
    больше ничего."""

    def test_only_identity_rows_are_written(self):
        session = _FakeSession([_Node("Иванов Пётр Сергеевич", created_at=1),
                                _Node("Иванов", created_at=2)])
        _run(session)
        allowed = (PUBLIC_MODELS.identity, PUBLIC_MODELS.member, PUBLIC_MODELS.candidate)
        assert session.added and all(isinstance(r, allowed) for r in session.added)

    def test_node_fields_are_unchanged(self):
        node = _Node("Гаврилова Марина Сергеевна", created_at=1)
        before = dict(vars(node))
        _run(_FakeSession([node]))
        assert dict(vars(node)) == before


class TestRepeatAndScope:
    def test_second_pass_adds_nothing(self):
        """Идемпотентность здесь не удобство: повтор, удваивающий состав,
        сделал бы «сколько документов про этого врача» неверным числом."""
        node = _Node("Гаврилова Марина Сергеевна", created_at=1)
        identity = PUBLIC_MODELS.identity(
            id=uuid.uuid4(), knowledge_user_id=TENANT, entity_type="person",
            canonical_label=node.canonical_label, normalized_key=node.normalized_key)
        member = PUBLIC_MODELS.member(
            id=uuid.uuid4(), knowledge_user_id=TENANT, identity_id=identity.id,
            node_id=node.id, matched_on=EntityIdentityMatch.NORMALIZED_LABEL)
        session = _FakeSession([node], members=[member], identities=[identity])
        report = _run(session)
        assert (report.already_resolved, report.members_created) == (1, 0)
        assert session.added == []

    def test_dry_run_reports_but_writes_nothing(self):
        session = _FakeSession([_Node("Гаврилова Марина Сергеевна", created_at=1)])
        report = _run(session, dry_run=True)
        assert report.members_created == 1
        assert session.added == [] and not session.flushed

    def test_nodes_outside_current_revisions_are_not_resolved(self):
        """§14.20: текущей может быть только дошедшая до READY ревизия.
        Строить личность из брошенного прогона значило бы отвечать по
        тому, что графом уже не является."""
        session = _FakeSession([_Node("Гаврилова Марина Сергеевна", created_at=1)])
        report = er.resolve_in(session, PUBLIC_MODELS, tenant_id=TENANT,
                               current_run_ids=set())
        assert report.as_dict()["members_created"] == 0

    def test_entity_without_type_is_skipped_not_invented(self):
        session = _FakeSession([_Node("Кто-то", entity_type=None, created_at=1)])
        report = _run(session)
        assert (report.nodes_seen, report.members_created) == (1, 0)
