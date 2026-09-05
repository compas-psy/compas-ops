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

from .health_schema import health_schema_configured, health_session, is_health_domain
from .relation_compiler import is_mentioned
from .semantic_publish import HEALTH_MODELS, PUBLIC_MODELS, publish_semantic_run
from .tenancy import bind_knowledge_user
from ..config import get_settings
from ..models import KnowledgeSemanticRun, KnowledgeSource, KnowledgeStatus
from ..models.base import SemanticNodeKind

#: Граница пилота из спеки. Не «сколько успеем»: больше — это уже R8
#: (полный backfill), который требует отдельного решения и ревью.
DEFAULT_LIMIT = 8

_FRONTMATTER_SEPARATOR = "\n---\n"


@dataclass
class SourceOutcome:
    """Одна строка сводки. Полей с содержимым здесь нет намеренно."""

    source_id: str
    domain: str
    run_status: str
    #: `None`, а не 0: пересчёт уже опубликованного не открывает файлы
    #: источников, и «не измеряли» — не то же самое, что «пусто».
    chars: int | None = None
    switched: bool = False
    windows_total: int = 0
    windows_processed: int = 0
    windows_failed: int = 0
    coverage_ratio: float = 0.0
    nodes: int = 0
    entities: int = 0
    atoms: int = 0
    edges: int = 0
    mentions_total: int = 0
    mentions_exact_span: int = 0
    mentions_without_span: int = 0
    error: str | None = None


@dataclass
class PilotReport:
    limit: int
    selected: int
    sources: list[SourceOutcome] = field(default_factory=list)
    #: Раскладка по всему пилоту, не по источнику: она отвечает на вопрос
    #: «почему компилятор молчит», а он про корпус целиком.
    by_kind: dict[str, dict[str, int]] = field(
        default_factory=lambda: {"entity_types": {}, "atom_kinds": {},
                                 "windows": {}, "grounding": {}})

    def add_kinds(self, breakdown: dict[str, dict[str, int]]) -> None:
        for group, counts in breakdown.items():
            target = self.by_kind[group]
            for key, count in counts.items():
                target[key] = target.get(key, 0) + count

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
            "entities": sum(s.entities for s in self.sources),
            "atoms": sum(s.atoms for s in self.sources),
            "edges": sum(s.edges for s in self.sources),
            "mentions": sum(s.mentions_total for s in self.sources),
            "mentions_exact_span": sum(s.mentions_exact_span for s in self.sources),
            "mentions_without_span": sum(s.mentions_without_span for s in self.sources),
        }

    def as_dict(self) -> dict:
        return {"limit": self.limit, "selected": self.selected,
                "totals": self.totals, "by_kind": self.by_kind,
                "sources": [asdict(s) for s in self.sources]}


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


def _count(session: Session, model, run_id: uuid.UUID, *extra) -> int:
    return session.scalar(select(func.count()).select_from(model)
                          .where(model.semantic_run_id == run_id, *extra)) or 0


def _counts(session: Session, models, run_id: uuid.UUID) -> dict[str, int]:
    total = _count(session, models.mention, run_id)
    exact = _count(session, models.mention, run_id, models.mention.char_start.is_not(None))
    nodes = _count(session, models.node, run_id)
    # Сущности отдельно от атомов: рёбра компилятор строит ТОЛЬКО из пар
    # атом×сущность (`relation_compiler.compile_relations`), поэтому «ноль
    # рёбер» читается по-разному при нуле атомов и при их изобилии. Одно
    # число `nodes` этот вопрос не различает.
    entities = _count(session, models.node, run_id,
                      models.node.kind == SemanticNodeKind.ENTITY)
    return {"nodes": nodes, "entities": entities, "atoms": nodes - entities,
            "edges": _count(session, models.edge, run_id),
            "mentions_total": total,
            "mentions_exact_span": exact,
            "mentions_without_span": total - exact}


def _by_kind(session: Session, models, run_id: uuid.UUID) -> dict[str, dict[str, int]]:
    """Раскладка узлов прогона по виду атома и по типу сущности.

    Оба — закрытые словари схемы (`SemanticNodeKind`, `entity_type`), не
    содержимое: по ним видно, ПОЧЕМУ компилятор не построил ребро
    (`involves` требует PERSON или ORGANIZATION, `located_at` — PLACE), и
    не видно, о ком и о чём документ.
    """
    rows = session.execute(
        select(models.node.kind, models.node.entity_type, func.count())
        .where(models.node.semantic_run_id == run_id)
        .group_by(models.node.kind, models.node.entity_type)).all()
    atoms: dict[str, int] = {}
    entities: dict[str, int] = {}
    for kind, entity_type, count in rows:
        if kind == SemanticNodeKind.ENTITY:
            entities[entity_type or "?"] = entities.get(entity_type or "?", 0) + count
        else:
            atoms[str(kind)] = atoms.get(str(kind), 0) + count
    return {"entity_types": entities, "atom_kinds": atoms}


def _window_mix(session: Session, models, run_id: uuid.UUID) -> dict[str, int]:
    """Есть ли в каждом окне и сущности, и атомы одновременно.

    Компилятор рёбер работает ВНУТРИ окна: он получает `entities` и
    `atoms` одного разбора и не видит соседние (`semantic_publish._process`
    зовёт `compile_relations` на каждое окно отдельно). Окно, где есть
    только сущности или только атомы, не может дать ни одного ребра,
    сколько бы правил в компилятор ни добавили.

    У реального документа это не редкость: «кто и где» стоит в шапке, а
    факты идут ниже — и попадают в разные окна.
    """
    rows = session.execute(
        select(models.mention.window_id, models.node.kind)
        .join(models.node, models.node.id == models.mention.node_id)
        .where(models.mention.semantic_run_id == run_id)).all()
    windows: dict[int | None, set[bool]] = {}
    for window_id, kind in rows:
        windows.setdefault(window_id, set()).add(kind == SemanticNodeKind.ENTITY)
    mix = {"both": 0, "entities_only": 0, "atoms_only": 0}
    for flags in windows.values():
        if flags == {True, False}:
            mix["both"] += 1
        elif flags == {True}:
            mix["entities_only"] += 1
        else:
            mix["atoms_only"] += 1
    return mix


def _grounding(session: Session, models, run_id: uuid.UUID, text: str) -> dict[str, int]:
    """Сколько пар «атом × сущность одного окна» проходят endpoint grounding.

    Это последнее, что стоит между материалом и ребром: компилятор
    требует, чтобы подпись сущности стояла ВНУТРИ цитаты самого атома
    (`relation_compiler.is_mentioned`), а не где-то в том же окне. На
    golden-корпусе окно — один плотный абзац, и требование выполняется
    само собой; у реального документа цитата атома может не называть
    участника вовсе.

    Цитата берётся по точному диапазону упоминания — тому самому, который
    R5.2 научился находить. Атом без диапазона в паре не участвует.
    Псевдонимы не учитываются (их отдельная таблица), поэтому число
    прошедших — нижняя оценка, и завышенным быть не может.

    Наружу уходят только два числа. Текст источника читается здесь же, на
    сервере, и остаётся здесь.
    """
    rows = session.execute(
        select(models.mention.window_id, models.node.kind, models.node.canonical_label,
               models.mention.char_start, models.mention.char_end)
        .join(models.node, models.node.id == models.mention.node_id)
        .where(models.mention.semantic_run_id == run_id)).all()
    entities: dict[int | None, list[str]] = {}
    atoms: dict[int | None, list[str]] = {}
    for window_id, kind, label, start, end in rows:
        if kind == SemanticNodeKind.ENTITY:
            entities.setdefault(window_id, []).append(label)
        elif start is not None and end is not None:
            atoms.setdefault(window_id, []).append(text[start:end])

    pairs = grounded = 0
    for window_id, quotes in atoms.items():
        for quote in quotes:
            for label in entities.get(window_id, ()):
                pairs += 1
                grounded += is_mentioned(quote, label)
    return {"pairs": pairs, "grounded": grounded}


def _breakdown(session: Session, models, run_id: uuid.UUID,
               text: str | None) -> dict[str, dict[str, int]]:
    breakdown = {**_by_kind(session, models, run_id),
                 "windows": _window_mix(session, models, run_id)}
    if text is not None:
        breakdown["grounding"] = _grounding(session, models, run_id, text)
    return breakdown


def run_counts(session: Session, run_id: uuid.UUID, *, domain: str,
               knowledge_user_id: uuid.UUID, text: str | None = None
               ) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Что прогон реально положил в граф: узлы, рёбра и упоминания.

    Отдельно считаются упоминания с ТОЧНЫМ диапазоном (R5.2, §30.8.5 F).
    Ненайденная цитата пишется как NULL, а не как границы окна, поэтому
    доля точных — обычный запрос, и именно она показывает, выполняется
    ли «resolves to exact span» на живом материале, а не на фикстурах.

    Считать надо в ТОЙ ЖЕ схеме, в которую писал `publish_semantic_run()`:
    health-источник кладёт узлы, рёбра и упоминания в health-зеркало,
    отдельным соединением и отдельной ролью (`semantic_publish._Models`).
    Первый прогон пилота 04.09.2026 считал публичную таблицу и показал
    `mentions_total = 0` там, где упоминания были, — «провенанса нет»
    вместо «смотрю не туда».
    """
    if is_health_domain(domain) and health_schema_configured():
        with health_session(knowledge_user_id) as graph:
            return (_counts(graph, HEALTH_MODELS, run_id),
                    _breakdown(graph, HEALTH_MODELS, run_id, text))
    return (_counts(session, PUBLIC_MODELS, run_id),
            _breakdown(session, PUBLIC_MODELS, run_id, text))


def inspect_published(session: Session, *, limit: int = DEFAULT_LIMIT,
                      domains: tuple[str, ...] | None = None,
                      knowledge_user_id: uuid.UUID | None = None) -> PilotReport:
    """Пересчитать уже опубликованное, ничего не публикуя.

    Нужен, когда вопрос не «сработает ли конвейер», а «что лежит в графе
    прямо сейчас»: повторять пилот ради одних только чисел значило бы
    заводить лишние ревизии источников и снова тратить час модели.

    Смотрит на ТЕКУЩУЮ ревизию источника (`current_semantic_run_id`) —
    ту, которую увидит запрос, а не последнюю попытку.
    """
    tenant_id = bind_knowledge_user(session, knowledge_user_id)
    sources = select_sources(session, limit=limit, domains=domains)
    report = PilotReport(limit=limit, selected=len(sources))

    for source in sources:
        run = (session.get(KnowledgeSemanticRun, source.current_semantic_run_id)
               if source.current_semantic_run_id else None)
        if run is None:
            report.sources.append(SourceOutcome(
                source_id=str(source.id), domain=source.domain,
                run_status="нет текущей ревизии"))
            continue
        text = source_text(source)
        counts, breakdown = run_counts(session, run.id, domain=source.domain,
                                       knowledge_user_id=tenant_id, text=text)
        report.add_kinds(breakdown)
        report.sources.append(SourceOutcome(
            source_id=str(source.id), domain=source.domain, run_status=str(run.status),
            chars=len(text) if text is not None else None,
            windows_total=run.windows_total, windows_processed=run.windows_processed,
            windows_failed=run.windows_failed,
            coverage_ratio=float(run.coverage_ratio or 0), **counts))
    return report


def run_pilot(session: Session, *, limit: int = DEFAULT_LIMIT,
              domains: tuple[str, ...] | None = None,
              knowledge_user_id: uuid.UUID | None = None) -> PilotReport:
    tenant_id = bind_knowledge_user(session, knowledge_user_id)
    sources = select_sources(session, limit=limit, domains=domains)
    report = PilotReport(limit=limit, selected=len(sources))

    for source in sources:
        text = source_text(source)
        if not text:
            report.sources.append(SourceOutcome(
                source_id=str(source.id), domain=source.domain, run_status="skipped",
                error="нет разобранного текста в Vault"))
            continue

        result = publish_semantic_run(session, source=source, text=text)
        session.flush()
        counts, breakdown = run_counts(session, result.run_id, domain=source.domain,
                                       knowledge_user_id=tenant_id, text=text)
        report.add_kinds(breakdown)
        report.sources.append(SourceOutcome(
            source_id=str(source.id), domain=source.domain, chars=len(text),
            run_status=str(result.status), switched=bool(result.switched),
            windows_total=result.windows_total,
            windows_processed=result.windows_processed,
            windows_failed=result.windows_failed,
            coverage_ratio=result.coverage_ratio, **counts))
    return report


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R5: пилотная сборка семантики")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--domains", default="",
                        help="через запятую; пусто — все домены")
    parser.add_argument("--out", default="", help="куда положить JSON-отчёт")
    parser.add_argument("--inspect-only", action="store_true",
                        help="не публиковать: пересчитать текущие ревизии тех же источников")
    args = parser.parse_args(argv)

    domains = tuple(d.strip() for d in args.domains.split(",") if d.strip()) or None
    # Тот же паттерн подключения, что у `worker_main.py`: отдельный
    # процесс, не отдельная конфигурация.
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    with sessionmaker(engine, expire_on_commit=False)() as session:
        if args.inspect_only:
            report = inspect_published(session, limit=args.limit, domains=domains)
        else:
            report = run_pilot(session, limit=args.limit, domains=domains)
            session.commit()

    payload = json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
