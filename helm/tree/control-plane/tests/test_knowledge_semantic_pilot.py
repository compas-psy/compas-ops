"""R5 — пилотная сборка: отбор источников, текст из Vault и приватность сводки.

Сам прогон по реальному корпусу проверяется на сервере: здесь доказывается
то, что можно доказать без базы и без содержимого, — в первую очередь что
сводка пилота структурно НЕ МОЖЕТ вынести наружу текст владельца.
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import fields

from helm_core.knowledge import semantic_pilot

_RUN_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


class _FakeSource:
    def __init__(self, source_path):
        self.source_path = source_path


class TestSourceText:
    def test_frontmatter_is_stripped(self, tmp_path):
        """Служебный YAML-блок — не документ. Оставить его значило бы
        подать извлекателю UUID и хэши как часть текста источника."""
        path = tmp_path / "s.md"
        path.write_text(
            "---\n"
            "id: 0f1e2d3c-0000-0000-0000-000000000000\n"
            "type: source\n"
            "domain: health\n"
            "---\n"
            "\n\n"
            "Приём вёл Иванов.\n",
            encoding="utf-8")
        assert semantic_pilot.source_text(_FakeSource(str(path))) == "Приём вёл Иванов.\n"

    def test_file_without_frontmatter_is_returned_as_is(self, tmp_path):
        path = tmp_path / "s.md"
        path.write_text("Просто текст.\n", encoding="utf-8")
        assert semantic_pilot.source_text(_FakeSource(str(path))) == "Просто текст.\n"

    def test_missing_file_or_path_is_none_not_an_exception(self, tmp_path):
        """У источника из `ingest_text()` файла на диске нет вовсе —
        пилот обязан пропустить его с пометкой, а не упасть на середине
        корпуса."""
        assert semantic_pilot.source_text(_FakeSource("")) is None
        assert semantic_pilot.source_text(_FakeSource(str(tmp_path / "нет.md"))) is None


class TestReportCarriesNoContent:
    """§5.2 CLAUDE.md и p.7 разбора R4: содержимое личного архива не
    уезжает в лог GitHub Actions. Проверяется структурой, а не
    внимательностью — поле с текстом нельзя добавить незаметно."""

    #: Всё, что попадает в отчёт: идентификаторы, домен, статусы и числа.
    ALLOWED = {
        "source_id", "domain", "chars", "run_status", "switched",
        "windows_total", "windows_processed", "windows_failed", "coverage_ratio",
        "nodes", "entities", "atoms", "edges", "mentions_total",
        "mentions_exact_span", "mentions_without_span", "error",
    }

    def test_outcome_has_no_field_outside_the_allowed_set(self):
        assert {f.name for f in fields(semantic_pilot.SourceOutcome)} == self.ALLOWED

    def test_no_outcome_field_holds_free_text(self):
        """`error` — единственная строка свободной формы, и она наша
        собственная, не из документа."""
        text_fields = {f.name for f in fields(semantic_pilot.SourceOutcome)
                       if f.type in ("str", "str | None")}
        assert text_fields == {"source_id", "domain", "run_status", "error"}


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Отдаёт заранее отсортированный список: сортировку делает БД, а
    проверяется здесь раскладка по доменам, а не ORDER BY."""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self, _query):
        return _FakeScalars(self._rows)


class _Src:
    def __init__(self, domain, name):
        self.domain = domain
        self.name = name


class TestDomainSpread:
    """§30.8.5 I требует минимум четыре непохожих домена. Пилот из десяти
    медицинских выписок доказал бы только про выписки, поэтому источники
    берутся по кругу, а не подряд."""

    def test_sources_are_taken_round_robin_across_domains(self):
        rows = [_Src("health", "h1"), _Src("health", "h2"), _Src("health", "h3"),
                _Src("purchases", "p1"), _Src("work", "w1"), _Src("work", "w2")]
        picked = semantic_pilot.select_sources(_FakeSession(rows), limit=4)
        assert [s.domain for s in picked] == ["health", "purchases", "work", "health"]

    def test_a_single_domain_corpus_still_fills_the_limit(self):
        """Раскладка — предпочтение, а не отказ работать: если у владельца
        всё в одном домене, пилот всё равно должен состояться."""
        rows = [_Src("health", f"h{i}") for i in range(5)]
        picked = semantic_pilot.select_sources(_FakeSession(rows), limit=3)
        assert [s.name for s in picked] == ["h0", "h1", "h2"]

    def test_limit_larger_than_the_corpus_takes_everything_once(self):
        rows = [_Src("health", "h1"), _Src("work", "w1")]
        picked = semantic_pilot.select_sources(_FakeSession(rows), limit=8)
        assert {s.name for s in picked} == {"h1", "w1"}
        assert len(picked) == 2, "источник не должен попасть в пилот дважды"


class TestPilotStaysWithinTheSpecifiedScale:
    def test_default_limit_is_within_the_five_to_ten_of_the_spec(self):
        """Спека R5: «5–10 real sources». Больше — это уже R8 (полный
        backfill), который требует отдельного решения."""
        assert 5 <= semantic_pilot.DEFAULT_LIMIT <= 10


class TestCountsUseTheSchemaThatWasWrittenTo:
    """Считать надо там, куда писал `publish_semantic_run()`.

    Первый прогон пилота 04.09.2026 считал публичную таблицу для
    health-источника и отчитался `mentions_total = 0` там, где упоминания
    были: «провенанса нет» вместо «смотрю не туда». Проверяется сам
    выбор схемы — он должен совпадать с выбором публикации
    (`is_health_domain(...) and health_schema_configured()`), а не быть
    похожим на него.
    """

    ZEROS = {"nodes": 0, "entities": 0, "atoms": 0, "edges": 0,
             "mentions_total": 0, "mentions_exact_span": 0, "mentions_without_span": 0}

    def _spy(self, monkeypatch):
        seen = {}

        def fake_counts(session, models, run_id):
            seen["session"], seen["models"] = session, models
            return dict(self.ZEROS)

        monkeypatch.setattr(semantic_pilot, "_counts", fake_counts)
        monkeypatch.setattr(semantic_pilot, "_breakdown",
                            lambda *_a: {"entity_types": {}, "atom_kinds": {}, "windows": {}})
        return seen

    def _mirror(self, monkeypatch, *, configured, graph=None):
        monkeypatch.setattr(semantic_pilot, "health_schema_configured", lambda: configured)
        monkeypatch.setattr(semantic_pilot, "health_session",
                            lambda _uid: contextlib.nullcontext(graph))

    def test_health_source_is_counted_in_the_health_mirror(self, monkeypatch):
        seen = self._spy(monkeypatch)
        graph, public = object(), object()
        self._mirror(monkeypatch, configured=True, graph=graph)
        semantic_pilot.run_counts(public, _RUN_ID, domain="health",
                                  knowledge_user_id=_USER_ID)
        assert seen["models"] is semantic_pilot.HEALTH_MODELS
        assert seen["session"] is graph

    def test_public_source_stays_in_the_public_session(self, monkeypatch):
        seen = self._spy(monkeypatch)
        public = object()
        self._mirror(monkeypatch, configured=True, graph=object())
        semantic_pilot.run_counts(public, _RUN_ID, domain="work",
                                  knowledge_user_id=_USER_ID)
        assert seen["models"] is semantic_pilot.PUBLIC_MODELS
        assert seen["session"] is public

    def test_without_the_mirror_health_is_counted_where_it_was_written(self, monkeypatch):
        """Зеркало не настроено — публикация пишет в public, значит и
        считать надо там же."""
        seen = self._spy(monkeypatch)
        public = object()
        self._mirror(monkeypatch, configured=False)
        semantic_pilot.run_counts(public, _RUN_ID, domain="health",
                                  knowledge_user_id=_USER_ID)
        assert seen["models"] is semantic_pilot.PUBLIC_MODELS
        assert seen["session"] is public


class TestBreakdownAccumulates:
    """Раскладка по видам — про весь пилот, а не про источник: вопрос
    «почему компилятор молчит» задаётся к корпусу целиком."""

    def test_counts_from_several_sources_are_summed(self):
        report = semantic_pilot.PilotReport(limit=2, selected=2)
        report.add_kinds({"entity_types": {"PERSON": 2}, "atom_kinds": {"fact": 3},
                          "windows": {"both": 1}})
        report.add_kinds({"entity_types": {"PERSON": 1, "CONCEPT": 4},
                          "atom_kinds": {"fact": 1, "event": 2},
                          "windows": {"both": 1, "atoms_only": 2}})
        assert report.by_kind == {"entity_types": {"PERSON": 3, "CONCEPT": 4},
                                  "atom_kinds": {"fact": 4, "event": 2},
                                  "windows": {"both": 2, "atoms_only": 2}}

    def test_two_reports_do_not_share_one_dict(self):
        """`field(default_factory=...)` здесь не формальность: общий
        словарь по умолчанию складывал бы разные пилоты в одну кучу."""
        first = semantic_pilot.PilotReport(limit=1, selected=1)
        first.add_kinds({"entity_types": {"PERSON": 1}, "atom_kinds": {}, "windows": {}})
        second = semantic_pilot.PilotReport(limit=1, selected=1)
        assert second.by_kind == {"entity_types": {}, "atom_kinds": {}, "windows": {}}
