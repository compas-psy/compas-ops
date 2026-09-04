"""R5 — пилотная сборка семантики на нескольких реальных источниках.

Спека: «5–10 real sources, staging then small commit. Manual/golden review
before full corpus.» Полный корпус — это R8, и он идёт только после ревью
результатов этого пилота.

Что здесь есть и чего нет. Здесь — отбор источников, прогон
`publish_semantic_run()` по каждому и СВОДКА. Здесь нет ни одной строки
содержимого: ни текста источника, ни подписей узлов, ни цитат. Отчёт
уезжает в лог GitHub Actions, а §5.2 CLAUDE.md и p.7 разбора R4 запрещают
выносить наружу содержимое личного архива. Всё, что печатается, —
идентификаторы, домены и числа; по ним видно, сработал ли конвейер, и не
видно, что в документах.

Читать содержимое для ревью владелец будет на сервере, где оно и лежит.
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from .semantic_publish import publish_semantic_run
from .tenancy import bind_knowledge_user
from ..config import get_settings
from ..models import KnowledgeNodeMention, KnowledgeSource, KnowledgeStatus

#: Граница пилота из спеки. Не «сколько успеем»: больше — это уже R8
#: (полный backfill), который требует отдельного решения и ревью.
DEFAULT_LIMIT = 8

_FRONTMATTER_SEPARATOR = "\n---\n"


@dataclass
class SourceOutcome:
    """Одна строка сводки. Полей с содержимым здесь нет намеренно."""

    source_id: str
    domain: str
    chars: int
    run_status: str
    switched: bool
    windows_total: int
    windows_processed: int
    windows_failed: int
    coverage_ratio: float
    nodes: int
    edges: int
    mentions_total: int
    mentions_exact_span: int
    mentions_without_span: int
    error: str | None = None


@dataclass
class PilotReport:
    limit: int
    selected: int
    sources: list[SourceOutcome] = field(default_factory=list)

    @property
    def totals(self) -> dict:
        return {
            "sources": len(self.sources),
            "ready": sum(1 for s in self.sources if s.run_status == "ready"),
            "degraded": sum(1 for s in self.sources if s.run_status == "degraded"),
            "failed": sum(1 for s in self.sources if s.error),
            "windows": sum(s.windows_total for s in self.sources),
            "windows_failed": sum(s.windows_failed for s in self.sources),
            "nodes": sum(s.nodes for s in self.sources),
            "edges": sum(s.edges for s in self.sources),
            "mentions": sum(s.mentions_total for s in self.sources),
            "mentions_exact_span": sum(s.mentions_exact_span for s in self.sources),
            "mentions_without_span": sum(s.mentions_without_span for s in self.sources),
        }

    def as_dict(self) -> dict:
        return {"limit": self.limit, "selected": self.selected,
                "totals": self.totals, "sources": [asdict(s) for s in self.sources]}


def source_text(source: KnowledgeSource) -> str | None:
    """Разобранный текст источника из Vault, без служебного фронтматтера.

    Берётся именно нормализованный текст (`source_path`), а не сырые байты
    (`raw_path`): семантику строят по разобранному документу, и ровно его
    видел бы владелец, открыв заметку. Фронтматтер отрезается — иначе
    извлекатель получил бы на вход UUID и хэши как часть «документа».
    """
    if not source.source_path:
        return None
    path = Path(source.source_path)
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("---\n"):
        _, separator, body = raw.partition(_FRONTMATTER_SEPARATOR)
        if separator:
            return body.lstrip("\n")
    return raw


def select_sources(session: Session, *, limit: int,
                   domains: tuple[str, ...] | None = None) -> list[KnowledgeSource]:
    """Отобрать источники пилота: живые, по одному на домен по кругу.

    Порядок детерминированный (домен, дата, id) — повтор пилота берёт те
    же документы, иначе сравнивать два прогона было бы не с чем.

    Раскладка по доменам не косметика: §30.8.5 I требует минимум четыре
    непохожих домена, и пилот из десяти медицинских выписок доказал бы
    только про них.
    """
    query = select(KnowledgeSource).where(KnowledgeSource.status == KnowledgeStatus.ACTIVE)
    if domains:
        query = query.where(KnowledgeSource.domain.in_(domains))
    rows = session.scalars(query.order_by(
        KnowledgeSource.domain, KnowledgeSource.created_at, KnowledgeSource.id)).all()

    by_domain: dict[str, list[KnowledgeSource]] = {}
    for row in rows:
        by_domain.setdefault(row.domain, []).append(row)

    picked: list[KnowledgeSource] = []
    while len(picked) < limit and any(by_domain.values()):
        for bucket in by_domain.values():
            if not bucket or len(picked) >= limit:
                continue
            picked.append(bucket.pop(0))
    return picked


def _mention_stats(session: Session, run_id: uuid.UUID) -> tuple[int, int, int]:
    """Сколько упоминаний прогона получили ТОЧНЫЙ диапазон (R5, §30.8.5 F).

    Ненайденная цитата пишется как NULL, а не как границы окна, поэтому
    доля точных считается обычным запросом — и именно она показывает,
    выполняется ли требование «resolves to exact span» на живом материале,
    а не только на фикстурах.
    """
    total = session.scalar(select(func.count()).select_from(KnowledgeNodeMention)
                           .where(KnowledgeNodeMention.semantic_run_id == run_id)) or 0
    exact = session.scalar(select(func.count()).select_from(KnowledgeNodeMention)
                           .where(KnowledgeNodeMention.semantic_run_id == run_id,
                                  KnowledgeNodeMention.char_start.is_not(None))) or 0
    return total, exact, total - exact


def run_pilot(session: Session, *, limit: int = DEFAULT_LIMIT,
              domains: tuple[str, ...] | None = None,
              knowledge_user_id: uuid.UUID | None = None) -> PilotReport:
    bind_knowledge_user(session, knowledge_user_id)
    sources = select_sources(session, limit=limit, domains=domains)
    report = PilotReport(limit=limit, selected=len(sources))

    for source in sources:
        text = source_text(source)
        if not text:
            report.sources.append(SourceOutcome(
                source_id=str(source.id), domain=source.domain, chars=0,
                run_status="skipped", switched=False, windows_total=0,
                windows_processed=0, windows_failed=0, coverage_ratio=0.0,
                nodes=0, edges=0, mentions_total=0,
                mentions_exact_span=0, mentions_without_span=0,
                error="нет разобранного текста в Vault"))
            continue

        result = publish_semantic_run(session, source=source, text=text)
        session.flush()
        mentions, exact, missing = _mention_stats(session, result.run_id)
        report.sources.append(SourceOutcome(
            source_id=str(source.id), domain=source.domain, chars=len(text),
            run_status=str(result.status), switched=bool(result.switched),
            windows_total=result.windows_total,
            windows_processed=result.windows_processed,
            windows_failed=result.windows_failed,
            coverage_ratio=result.coverage_ratio,
            nodes=result.nodes_created, edges=result.edges_created,
            mentions_total=mentions, mentions_exact_span=exact,
            mentions_without_span=missing))
    return report


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R5: пилотная сборка семантики")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--domains", default="",
                        help="через запятую; пусто — все домены")
    parser.add_argument("--out", default="", help="куда положить JSON-отчёт")
    args = parser.parse_args(argv)

    domains = tuple(d.strip() for d in args.domains.split(",") if d.strip()) or None
    # Тот же паттерн подключения, что у `worker_main.py`: отдельный
    # процесс, не отдельная конфигурация.
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    with sessionmaker(engine, expire_on_commit=False)() as session:
        report = run_pilot(session, limit=args.limit, domains=domains)
        session.commit()

    payload = json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
