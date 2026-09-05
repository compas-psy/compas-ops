"""R10 — приёмка на четырёх доменах: что она обязана доказать.

§30.8.5 I требует четыре непохожих класса через ОДНО ядро. Здесь
проверяется не качество извлечения (его меряет живой прогон), а
контракт самой приёмки: прогон идёт продакшн-путём, ничего не оставляет
в корпусе и не засчитывает себе успех, если что-то из этого нарушено.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from helm_core.knowledge import r10_acceptance as r10
from helm_core.knowledge.r10_fixtures import FIXTURES, FixtureOutcome

TENANT = uuid.UUID("00000000-0000-0000-0000-00000000beef")


class _FakeSession:
    def __init__(self, *, counts=(10, 20), raise_on=()):
        self.added = []
        self.rollbacks = 0
        self.flushes = 0
        self._counts = list(counts)
        self.raise_on = set(raise_on)

    def scalar(self, _query):
        return self._counts[0]

    def scalars(self, _query):
        return _Result([])

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        self.flushes += 1

    def rollback(self):
        self.rollbacks += 1


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _Published:
    def __init__(self, *, fail_keys=()):
        self.calls = []
        self.fail_keys = set(fail_keys)
        self.paths = []

    def __call__(self, session, *, source, text):
        self.calls.append(source.domain)
        self.paths.append(source.source_path)
        if source.original_filename.removesuffix(".md") in self.fail_keys:
            raise RuntimeError("модель недоступна")
        return type("R", (), {"run_id": uuid.uuid4(), "windows_total": 1,
                              "windows_failed": 0, "coverage_ratio": 1.0})()


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    monkeypatch.setattr(r10, "bind_knowledge_user", lambda session, tid=None: TENANT)
    monkeypatch.setattr(r10, "_measure", lambda *a, **k: None)


def test_прогоняются_все_четыре_класса(monkeypatch):
    published = _Published()
    monkeypatch.setattr(r10, "publish_semantic_run", published)
    report = r10.run_fixtures(_FakeSession())
    assert len(report.fixtures) == 4
    assert {f.acceptance_class for f in report.fixtures} == {
        "health visit", "project meeting/decision",
        "purchase/warranty", "lecture/concept"}


def test_фикстуры_не_health(monkeypatch):
    # Если бы ядро было медицинским, документ о покупке холодильника не
    # дал бы ни одного узла. Домен фикстур — часть проверки.
    published = _Published()
    monkeypatch.setattr(r10, "publish_semantic_run", published)
    r10.run_fixtures(_FakeSession())
    assert set(published.calls) == {"library"}
    assert all(f.domain != "health" for f in FIXTURES)


def test_всё_откатывается(monkeypatch):
    monkeypatch.setattr(r10, "publish_semantic_run", _Published())
    session = _FakeSession()
    r10.run_fixtures(session)
    assert session.rollbacks == 1


def test_откат_и_уборка_при_падении(monkeypatch):
    published = _Published(fail_keys={"purchase_warranty"})
    monkeypatch.setattr(r10, "publish_semantic_run", published)
    session = _FakeSession()
    report = r10.run_fixtures(session)
    assert session.rollbacks == 1
    failed = [f for f in report.fixtures if f.key == "purchase_warranty"][0]
    assert failed.error == "RuntimeError"
    assert not failed.passed
    # Временный каталог не остаётся на диске ни при каком исходе.
    assert not Path(published.paths[0]).parent.exists()


def test_изменившийся_корпус_снимает_успех():
    report = r10.R10Report(sources_before=10, sources_after=11,
                           nodes_before=20, nodes_after=20)
    report.fixtures = [FixtureOutcome(key="k", acceptance_class="c", coverage=1.0)]
    assert not report.passed


def test_пропавшая_подпись_снимает_успех():
    outcome = FixtureOutcome(key="k", acceptance_class="c", coverage=1.0)
    outcome.labels_missing = ["Северцев Артём Игоревич"]
    assert not outcome.passed


def test_проваленное_окно_снимает_успех():
    outcome = FixtureOutcome(key="k", acceptance_class="c", coverage=1.0,
                             windows_failed=1)
    assert not outcome.passed


def test_неполное_покрытие_снимает_успех():
    outcome = FixtureOutcome(key="k", acceptance_class="c", coverage=0.94)
    assert not outcome.passed


def test_успех_требует_и_фикстур_и_неизменного_корпуса():
    report = r10.R10Report(sources_before=10, sources_after=10,
                           nodes_before=20, nodes_after=20)
    report.fixtures = [FixtureOutcome(key="k", acceptance_class="c", coverage=1.0)]
    assert report.passed
    # Пустой список фикстур успехом не считается: «ничего не проверено»
    # не то же самое, что «всё прошло».
    report.fixtures = []
    assert not report.passed
