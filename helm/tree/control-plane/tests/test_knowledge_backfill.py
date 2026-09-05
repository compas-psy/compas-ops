"""R8 — перенос корпуса: что пропускается, что доразбирается, где рвётся.

`HELM_FINAL_v4.0_RESCUE §R8`: «Idempotent, resumable, per-source
semantic revision. Preserve RAW/L1.» Каждое из четырёх слов проверяется
отдельно: свойство без теста держится на памяти следующего правщика.

База не нужна — перенос выбирает источники и решает, разбирать ли их;
разбор подменён, потому что проверяется не он, а решение.
"""

from __future__ import annotations

import uuid

import pytest

from helm_core.knowledge import backfill as bf
from helm_core.models.base import KnowledgeStatus, SemanticRunStatus

TENANT = uuid.UUID("00000000-0000-0000-0000-00000000beef")


class _Run:
    def __init__(self, version=bf.SEMANTIC_VERSION, status=SemanticRunStatus.READY):
        self.id = uuid.uuid4()
        self.semantic_version = version
        self.status = status


class _Source:
    def __init__(self, domain="health", *, run=None, created_at=0):
        self.id = uuid.uuid4()
        self.knowledge_user_id = TENANT
        self.domain = domain
        self.status = KnowledgeStatus.ACTIVE
        self.created_at = created_at
        self.current_semantic_run_id = run.id if run else None


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, sources, runs=()):
        self.sources = list(sources)
        self.runs = {r.id: r for r in runs}
        self.commits = 0
        self.rollbacks = 0

    def scalars(self, _query):
        return _Result(self.sources)

    def get(self, _model, run_id):
        return self.runs.get(run_id)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _Published:
    """Подменённая публикация: считает вызовы и отдаёт годную ревизию."""

    def __init__(self, *, raises_on=()):
        self.calls = []
        self.raises_on = set(raises_on)

    def __call__(self, session, *, source, text, semantic_version):
        self.calls.append(source.id)
        if source.id in self.raises_on:
            raise ConnectionError("модель недоступна: <текст документа>")
        return type("R", (), {
            "status": SemanticRunStatus.READY, "windows_total": 1, "windows_failed": 0,
            "nodes_created": 3, "edges_created": 0, "coverage_ratio": 1.0,
            "switched": True})()


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    monkeypatch.setattr(bf, "bind_knowledge_user", lambda session, tid=None: TENANT)
    monkeypatch.setattr(bf, "source_text", lambda source: "текст источника")


def _run(session, published, **kwargs):
    return bf.run_backfill(session, **kwargs), published


def test_разобранный_текущей_версией_источник_пропускается(monkeypatch):
    run = _Run()
    session = _FakeSession([_Source(run=run)], runs=[run])
    published = _Published()
    monkeypatch.setattr(bf, "publish_semantic_run", published)
    report = bf.run_backfill(session)
    assert published.calls == []
    assert report.already_current == 1
    assert report.processed == 0


def test_ревизия_прошлой_версии_разбирается_заново(monkeypatch):
    run = _Run(version=bf.SEMANTIC_VERSION - 1)
    source = _Source(run=run)
    session = _FakeSession([source], runs=[run])
    published = _Published()
    monkeypatch.setattr(bf, "publish_semantic_run", published)
    report = bf.run_backfill(session)
    assert published.calls == [source.id]
    assert report.ready == 1


def test_деградировавшая_ревизия_готовой_не_считается(monkeypatch):
    # §14.20: текущей становится только READY. Считать DEGRADED
    # разобранным значило бы оставить корпус недоразобранным молча.
    run = _Run(status=SemanticRunStatus.DEGRADED)
    source = _Source(run=run)
    session = _FakeSession([source], runs=[run])
    published = _Published()
    monkeypatch.setattr(bf, "publish_semantic_run", published)
    bf.run_backfill(session)
    assert published.calls == [source.id]


def test_ограничение_числом_источников(monkeypatch):
    sources = [_Source(created_at=i) for i in range(4)]
    session = _FakeSession(sources)
    published = _Published()
    monkeypatch.setattr(bf, "publish_semantic_run", published)
    report = bf.run_backfill(session, limit=2)
    assert len(published.calls) == 2
    assert report.todo == 4
    assert report.stopped_by == "limit"


def test_бюджет_проверяется_до_начала_источника(monkeypatch):
    sources = [_Source(created_at=i) for i in range(3)]
    session = _FakeSession(sources)
    published = _Published()
    monkeypatch.setattr(bf, "publish_semantic_run", published)
    # Нулевой бюджет: ни один источник не начат — начатый пришлось бы
    # доводить до конца, и «бюджет» перестал бы быть бюджетом.
    report = bf.run_backfill(session, budget_seconds=0)
    assert published.calls == []
    assert report.stopped_by == "budget"


def test_коммит_на_каждый_источник(monkeypatch):
    # Возобновляемость держится на этом: оборванный прогон оставляет
    # разобранное разобранным.
    sources = [_Source(created_at=i) for i in range(3)]
    session = _FakeSession(sources)
    monkeypatch.setattr(bf, "publish_semantic_run", _Published())
    bf.run_backfill(session)
    assert session.commits == 3


def test_падение_одного_источника_не_рушит_перенос(monkeypatch):
    sources = [_Source(created_at=i) for i in range(3)]
    session = _FakeSession(sources)
    published = _Published(raises_on=[sources[1].id])
    monkeypatch.setattr(bf, "publish_semantic_run", published)
    report = bf.run_backfill(session)
    assert len(published.calls) == 3
    assert report.failed == 1
    assert session.rollbacks == 1
    assert session.commits == 2


def test_в_отчёте_об_ошибке_только_имя_класса(monkeypatch):
    # Текст ошибки модели может нести кусок документа, а он
    # медицинский (§5.2). Наружу уходит имя класса и ничего больше.
    source = _Source()
    session = _FakeSession([source])
    monkeypatch.setattr(bf, "publish_semantic_run", _Published(raises_on=[source.id]))
    report = bf.run_backfill(session)
    payload = report.as_dict()
    assert payload["outcomes"][0]["error"] == "ConnectionError"
    assert "документ" not in repr(payload)


def test_повтор_на_разобранном_корпусе_ничего_не_делает(monkeypatch):
    run = _Run()
    session = _FakeSession([_Source(run=run), _Source(run=run, created_at=1)], runs=[run])
    published = _Published()
    monkeypatch.setattr(bf, "publish_semantic_run", published)
    first = bf.run_backfill(session).as_dict()
    second = bf.run_backfill(session).as_dict()
    # `seconds` — единственное, чему разрешено отличаться.
    first.pop("seconds"), second.pop("seconds")
    assert published.calls == []
    assert first == second
    assert first["processed"] == 0
    assert session.commits == 0


def test_план_считает_тем_же_условием_что_и_перенос():
    ready = _Run()
    old = _Run(version=bf.SEMANTIC_VERSION - 1)
    session = _FakeSession(
        [_Source(run=ready), _Source("finance", run=old, created_at=1), _Source(created_at=2)],
        runs=[ready, old])
    payload = bf.plan(session)
    assert payload["sources_total"] == 3
    assert payload["already_current"] == 1
    assert payload["todo"] == 2
    assert payload["todo_by_domain"] == {"finance": 1, "health": 1}
