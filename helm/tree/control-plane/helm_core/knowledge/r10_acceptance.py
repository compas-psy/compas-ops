"""R10 — приёмка §30.8.5 I: одно ядро на четырёх непохожих доменах.

> At least 4 non-identical semantic domains/classes… Same core node/edge
> pipeline; no health-only retrieval implementation accepted.

Проверка идёт **тем же путём, что и продакшн**: `publish_semantic_run`
на текст фикстуры, те же окна, тот же извлекатель, тот же провенанс.
Отдельного «тестового движка» здесь нет — иначе проверялся бы он, а не
ядро.

**Ничего не остаётся в корпусе.** Все четыре фикстуры публикуются в
одной транзакции, измеряются и откатываются. Ни одного источника, ни
одного узла, ни одной ревизии после прогона не появляется — свойство
проверяется самим прогоном (счётчики до и после) и тестом. Загружать
синтетику в живой корпус владельца ради приёмки нельзя: она осталась бы
там навсегда и попадала бы в ответы.

Домен фикстур — `library`, не `health`. Это часть проверки: если бы
ядро было медицинским, документ о покупке холодильника не дал бы ни
одного узла.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from .r10_fixtures import FIXTURES, Fixture, FixtureOutcome
from .semantic_publish import PUBLIC_MODELS, normalize_key, publish_semantic_run
from .tenancy import bind_knowledge_user
from ..config import get_settings
from ..models import KnowledgeSource
from ..models.base import KnowledgeStatus, SemanticNodeKind


@dataclass
class R10Report:
    fixtures: list[FixtureOutcome] = field(default_factory=list)
    sources_before: int = 0
    sources_after: int = 0
    nodes_before: int = 0
    nodes_after: int = 0

    @property
    def passed(self) -> bool:
        """Пройдено, только если пройдены все четыре И корпус не изменился."""
        return (bool(self.fixtures)
                and all(f.passed for f in self.fixtures)
                and self.sources_before == self.sources_after
                and self.nodes_before == self.nodes_after)

    def as_dict(self) -> dict:
        return {"passed": self.passed,
                "corpus_unchanged": (self.sources_before == self.sources_after
                                     and self.nodes_before == self.nodes_after),
                "sources_before_after": [self.sources_before, self.sources_after],
                "nodes_before_after": [self.nodes_before, self.nodes_after],
                "fixtures": [vars(f) for f in self.fixtures]}


def _measure(session: Session, fixture: Fixture, run_id: uuid.UUID,
             outcome: FixtureOutcome) -> None:
    nodes = session.scalars(
        select(PUBLIC_MODELS.node)
        .where(PUBLIC_MODELS.node.semantic_run_id == run_id)).all()
    outcome.nodes = len(nodes)
    outcome.entities = sum(1 for n in nodes if n.kind == SemanticNodeKind.ENTITY)
    outcome.kinds_found = sorted({str(n.kind).lower() for n in nodes})
    outcome.kinds_missing = [k for k in fixture.expect_kinds
                             if k not in outcome.kinds_found]

    # Подпись считается извлечённой по тому же нормализатору, каким
    # ядро сливает сущности. Сравнивать иначе значило бы проверять не
    # тот ключ, по которому работает продакшн.
    keys = {normalize_key(n.canonical_label) for n in nodes}
    for label in fixture.expect_labels:
        (outcome.labels_found if normalize_key(label) in keys
         else outcome.labels_missing).append(label)

    dates = {n.occurred_at_start.date().isoformat() for n in nodes
             if n.occurred_at_start is not None}
    for date in fixture.expect_dates:
        (outcome.dates_found if date in dates else outcome.dates_missing).append(date)

    outcome.mentions_exact_span = session.scalar(
        select(func.count()).select_from(PUBLIC_MODELS.mention)
        .where(PUBLIC_MODELS.mention.semantic_run_id == run_id,
               PUBLIC_MODELS.mention.char_start.is_not(None))) or 0
    outcome.edges = session.scalar(
        select(func.count()).select_from(PUBLIC_MODELS.edge)
        .where(PUBLIC_MODELS.edge.semantic_run_id == run_id)) or 0


def run_fixtures(session: Session, *,
                 knowledge_user_id: uuid.UUID | None = None) -> R10Report:
    """Прогнать четыре фикстуры и откатить всё, что они записали."""
    tenant_id = bind_knowledge_user(session, knowledge_user_id)

    def count(model) -> int:
        return session.scalar(select(func.count()).select_from(model)) or 0

    report = R10Report(sources_before=count(KnowledgeSource),
                       nodes_before=count(PUBLIC_MODELS.node))
    workdir = Path(tempfile.mkdtemp(prefix="r10-fixtures-"))
    try:
        for fixture in FIXTURES:
            outcome = FixtureOutcome(key=fixture.key,
                                     acceptance_class=fixture.acceptance_class)
            report.fixtures.append(outcome)
            path = workdir / f"{fixture.key}.md"
            path.write_text(fixture.text, encoding="utf-8")
            source = KnowledgeSource(
                knowledge_user_id=tenant_id, domain=fixture.domain,
                sha256=hashlib.sha256(fixture.text.encode("utf-8")).hexdigest(),
                raw_path=str(path), source_path=str(path),
                original_filename=f"{fixture.key}.md", mime_type="text/markdown",
                parser="fixture", status=KnowledgeStatus.ACTIVE)
            session.add(source)
            session.flush()
            try:
                result = publish_semantic_run(session, source=source, text=fixture.text)
            except Exception as exc:  # noqa: BLE001 — одна фикстура не рушит приёмку
                outcome.error = type(exc).__name__
                continue
            outcome.windows_total = result.windows_total
            outcome.windows_failed = result.windows_failed
            outcome.coverage = result.coverage_ratio
            _measure(session, fixture, result.run_id, outcome)
    finally:
        # Откат — часть контракта, а не уборка на всякий случай.
        session.rollback()
        shutil.rmtree(workdir, ignore_errors=True)

    bind_knowledge_user(session, tenant_id)
    report.sources_after = count(KnowledgeSource)
    report.nodes_after = count(PUBLIC_MODELS.node)
    return report


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R10: приёмка на четырёх доменах")
    parser.add_argument("--out", default="", help="куда положить полный отчёт")
    args = parser.parse_args(argv)

    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    with sessionmaker(engine, expire_on_commit=False)() as session:
        report = run_fixtures(session)
        session.rollback()

    payload = json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
