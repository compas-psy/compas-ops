"""R5 — пилотная сборка: отбор источников, текст из Vault и приватность сводки.

Сам прогон по реальному корпусу проверяется на сервере: здесь доказывается
то, что можно доказать без базы и без содержимого, — в первую очередь что
сводка пилота структурно НЕ МОЖЕТ вынести наружу текст владельца.
"""

from __future__ import annotations

from dataclasses import fields

from helm_core.knowledge import semantic_pilot


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
        "nodes", "edges", "mentions_total", "mentions_exact_span",
        "mentions_without_span", "error",
    }

    def test_outcome_has_no_field_outside_the_allowed_set(self):
        assert {f.name for f in fields(semantic_pilot.SourceOutcome)} == self.ALLOWED

    def test_no_outcome_field_holds_free_text(self):
        """`error` — единственная строка свободной формы, и она наша
        собственная, не из документа."""
        text_fields = {f.name for f in fields(semantic_pilot.SourceOutcome)
                       if f.type in ("str", "str | None")}
        assert text_fields == {"source_id", "domain", "run_status", "error"}


class TestPilotStaysWithinTheSpecifiedScale:
    def test_default_limit_is_within_the_five_to_ten_of_the_spec(self):
        """Спека R5: «5–10 real sources». Больше — это уже R8 (полный
        backfill), который требует отдельного решения."""
        assert 5 <= semantic_pilot.DEFAULT_LIMIT <= 10
