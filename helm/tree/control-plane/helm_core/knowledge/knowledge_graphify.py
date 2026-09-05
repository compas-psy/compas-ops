"""R9 — KnowledgeGraphify: производный индекс поверх semantic-v2.

`HELM v4.0 §14.11`, инвариант K11: это **не** `tools/graphify.py` и не
`graph/ops` — те навигируют агента по коду и документации `compas-ops`.
Здесь пользовательский семантический граф: свой модуль, свои пути, свой
вход.

Четыре свойства, ради которых шаг существует, держатся устройством.

**Отдельность.** Ни одной строки общего с RepoGraphify: другой модуль,
другой корень на диске, другой вход (узлы и рёбра semantic-v2, не файлы
репозитория).

**По пользователю и по контуру безопасности** (§14.11.3). Health-граф
пишется в приватное дерево, общий — в общий Vault. Процесс без прав на
контур физически не встретит его разметку: корень выводится тем же
`scope_root()`, что уже разводит исходники (§14.16).

**Производность** (§14.11.4). Канонична база: узлы, рёбра, упоминания,
провенанс. Удаление всего дерева `derived/graphify` не теряет ничего —
следующая сборка восстановит его из базы. Поэтому здесь нет ни одной
записи в канонические таблицы: модуль только читает.

**Никогда не истина.** Граф не добавляет фактов. Он перекладывает уже
доказанное в форму, удобную для обхода.

## Что даёт связность, когда рёбер ноль

R5 измерил: на реальном корпусе компилятор связей даёт ноль рёбер, и
это его честный fail-close (`docs/R5_PILOT_2026-09-05.md`). Граф из
одних узлов бесполезен — обходить нечего.

Но связность есть, и она доказана: **состав канонической личности**
(R6). Один врач, упомянутый в трёх выписках, — это три узла разных
источников, сведённые в одну личность строкой состава с доказательством
(`matched_on`). Путь «документ А → личность → документ Б» существует и
опирается не на догадку модели, а на дословное совпадение подписи.

Такие связи попадают в производный граф отдельным видом `SAME_AS` с
пометкой `derived: true`. Они **не** записываются в `knowledge_edges` и
не выдаются за извлечённые связи: канонический реестр §14.9 не
расширяется производной навигацией.
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from .health_schema import health_schema_configured, health_session, is_health_domain
from .semantic_publish import HEALTH_MODELS, PUBLIC_MODELS
from .tenancy import bind_knowledge_user
from .vault import scope_root
from .ingest import DEFAULT_VAULT_ROOT
from ..config import get_settings
from ..models import KnowledgeSource
from ..models.base import SemanticNodeStatus

#: Версия формата производного индекса. Меняется, когда меняется форма
#: `graph.json`, — чтобы читатель отличал старую сборку от новой, а не
#: гадал по содержимому.
GRAPHIFY_FORMAT_VERSION = 1

#: Вид связи, которого нет в каноническом реестре §14.9 и не должно
#: быть: он существует только в производном индексе.
DERIVED_SAME_AS = "SAME_AS"


@dataclass
class ScopeReport:
    scope: str
    nodes: int = 0
    edges_canonical: int = 0
    links_derived: int = 0
    identities: int = 0
    #: Узлы, до которых есть путь хотя бы в один узел ДРУГОГО источника.
    #: То самое, ради чего граф и строится: «документ А → личность →
    #: документ Б». Ноль здесь означает, что обходить нечего, и это
    #: измерение, а не отговорка.
    cross_source_nodes: int = 0
    markdown_written: int = 0
    path: str = ""


@dataclass
class GraphifyReport:
    dry_run: bool = False
    scopes: list[ScopeReport] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"dry_run": self.dry_run, "format_version": GRAPHIFY_FORMAT_VERSION,
                "scopes": [vars(s) for s in self.scopes]}


def graphify_root(vault_root: str, *, domain: str, knowledge_user_id: uuid.UUID) -> Path:
    """Корень производного графа для контура (§14.11.3, §14.16).

    Для health путь целиком лежит внутри приватного дерева — ровно так,
    как его рисует §14.16; для остальных доменов раскладка по
    пользователю нужна явно, потому что общий Vault один на всех.
    """
    root = scope_root(vault_root, domain=domain, knowledge_user_id=knowledge_user_id)
    if root == vault_root:
        return Path(root) / "derived" / "graphify" / "users" / str(knowledge_user_id) / "general"
    return Path(root) / "derived" / "graphify"


def semantic_root(vault_root: str, *, domain: str, knowledge_user_id: uuid.UUID) -> Path:
    """Куда ложится Markdown семантических узлов (§14.10, §14.16)."""
    root = scope_root(vault_root, domain=domain, knowledge_user_id=knowledge_user_id)
    if root == vault_root:
        return Path(root) / "semantic" / "users" / str(knowledge_user_id)
    return Path(root) / "semantic"


def _wikilink(node_id: uuid.UUID, kind: str, label: str) -> str:
    """`[[kind-<uuid>|подпись]]` — §14.10: устойчивый идентификатор с
    человеческим псевдонимом, чтобы однофамильцы не сливались ссылкой."""
    return f"[[{kind.lower()}-{node_id}|{label}]]"


def _frontmatter(node, mentions) -> list[str]:
    lines = [f"id: {str(node.kind).lower()}:{node.id}", f"type: {node.kind}"]
    if node.subtype:
        lines.append(f"subtype: {node.subtype}")
    if node.entity_type:
        lines.append(f"entity_type: {node.entity_type}")
    if node.occurred_at_start:
        lines.append(f"occurred_at: {node.occurred_at_start.date().isoformat()}")
    if node.date_precision:
        lines.append(f"date_precision: {node.date_precision}")
    for mention in mentions:
        span = ("" if mention.char_start is None
                else f" span: {mention.char_start}-{mention.char_end}")
        lines.append(f"source: {mention.source_id} window: {mention.window_id}{span}")
    lines.append(f"semantic_run: {node.semantic_run_id}")
    lines.append("trust: extracted")
    return lines


def _markdown(node, mentions, outgoing) -> str:
    """Детерминированная разметка одного узла.

    Ни одной метки времени сборки: файл обязан быть побайтово тем же,
    пока не изменились сами данные, — иначе «пересоберём и сравним»
    перестаёт быть проверкой.
    """
    body = [f"# {node.canonical_label}"]
    if node.statement_text:
        body.append("")
        body.append(node.statement_text)
    if outgoing:
        body.append("")
        for relation_type, role, target_id, target_kind, target_label in outgoing:
            suffix = f" (роль: {role})" if role else ""
            body.append(f"- {relation_type}{suffix} → "
                        f"{_wikilink(target_id, target_kind, target_label)}")
    return "---\n" + "\n".join(_frontmatter(node, mentions)) + "\n---\n\n" + "\n".join(body) + "\n"


def build_scope(graph: Session, models, *, tenant_id: uuid.UUID, scope: str,
                run_ids: set[uuid.UUID], root: Path, markdown_root: Path,
                dry_run: bool) -> ScopeReport:
    """Собрать производный граф и разметку одного контура."""
    report = ScopeReport(scope=scope, path=str(root))
    if not run_ids:
        return report

    nodes = graph.scalars(
        select(models.node)
        .where(models.node.knowledge_user_id == tenant_id,
               models.node.status == SemanticNodeStatus.ACTIVE,
               models.node.semantic_run_id.in_(run_ids))
        .order_by(models.node.id)).all()
    by_id = {node.id: node for node in nodes}
    report.nodes = len(nodes)

    mentions_by_node: dict[uuid.UUID, list] = {}
    for mention in graph.scalars(
            select(models.mention)
            .where(models.mention.knowledge_user_id == tenant_id,
                   models.mention.semantic_run_id.in_(run_ids))
            .order_by(models.mention.node_id, models.mention.char_start)).all():
        mentions_by_node.setdefault(mention.node_id, []).append(mention)

    edges = graph.scalars(
        select(models.edge)
        .where(models.edge.knowledge_user_id == tenant_id,
               models.edge.semantic_run_id.in_(run_ids))
        .order_by(models.edge.id)).all()
    report.edges_canonical = len(edges)

    # Состав канонической личности — доказанная связность (R6). В
    # каноническом реестре §14.9 её нет и быть не должно; здесь она
    # помечена производной.
    members = graph.execute(
        select(models.member.identity_id, models.member.node_id)
        .where(models.member.knowledge_user_id == tenant_id)
        .order_by(models.member.identity_id, models.member.node_id)).all()
    by_identity: dict[uuid.UUID, list[uuid.UUID]] = {}
    for identity_id, node_id in members:
        if node_id in by_id:
            by_identity.setdefault(identity_id, []).append(node_id)
    report.identities = len(by_identity)

    derived: list[dict] = []
    for identity_id, node_ids in sorted(by_identity.items(), key=lambda kv: str(kv[0])):
        for one in node_ids:
            for other in node_ids:
                if one != other:
                    derived.append({"from": str(one), "to": str(other),
                                    "type": DERIVED_SAME_AS, "identity": str(identity_id),
                                    "derived": True})
    report.links_derived = len(derived)

    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(str(edge.from_node_id), set()).add(str(edge.to_node_id))
        adjacency.setdefault(str(edge.to_node_id), set()).add(str(edge.from_node_id))
    for link in derived:
        adjacency.setdefault(link["from"], set()).add(link["to"])

    # Сколько узлов реально связаны с узлом ДРУГОГО источника. Это и
    # есть польза графа; ноль означает, что обходить нечего.
    source_of: dict[uuid.UUID, uuid.UUID | None] = {
        node_id: (mentions[0].source_id if mentions else None)
        for node_id, mentions in mentions_by_node.items()}
    for node_id_str, neighbours in adjacency.items():
        mine = source_of.get(uuid.UUID(node_id_str))
        if any(source_of.get(uuid.UUID(n)) not in (None, mine) for n in neighbours):
            report.cross_source_nodes += 1

    outgoing_by_node: dict[uuid.UUID, list] = {}
    for edge in edges:
        target = by_id.get(edge.to_node_id)
        if target is None:
            continue
        outgoing_by_node.setdefault(edge.from_node_id, []).append(
            (edge.relation_type, edge.role, target.id, target.kind, target.canonical_label))

    payload = {
        "format_version": GRAPHIFY_FORMAT_VERSION,
        "scope": scope,
        "knowledge_user_id": str(tenant_id),
        "nodes": [{"id": str(n.id), "kind": str(n.kind), "subtype": n.subtype,
                   "entity_type": n.entity_type, "label": n.canonical_label,
                   "occurred_at": (n.occurred_at_start.date().isoformat()
                                   if n.occurred_at_start else None),
                   "sources": sorted({str(m.source_id)
                                      for m in mentions_by_node.get(n.id, [])})}
                  for n in nodes],
        "edges": [{"from": str(e.from_node_id), "to": str(e.to_node_id),
                   "type": e.relation_type, "role": e.role,
                   "source_id": str(e.source_id) if e.source_id else None,
                   "derived": False} for e in edges],
        "derived_links": derived,
    }

    if dry_run:
        return report

    root.mkdir(parents=True, exist_ok=True)
    (root / "graph.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    # 0640, не 0600: приватное дерево заведено `2770 root:helm-health`
    # и обязано оставаться читаемой .md-структурой для Obsidian и
    # приложения ЗАПИСКИ (SPEC_DEVIATION D-1, отдельное требование
    # владельца). Setgid-каталог отдаёт файлу группу `helm-health`;
    # 0600 отрезал бы владельца от его же разметки.
    (root / "graph.json").chmod(0o640)

    for node in nodes:
        target = markdown_root / str(node.kind).lower()
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{node.id}.md"
        path.write_text(_markdown(node, mentions_by_node.get(node.id, []),
                                  outgoing_by_node.get(node.id, [])), encoding="utf-8")
        path.chmod(0o640)
        report.markdown_written += 1
    return report


def rebuild_all(session: Session, *, knowledge_user_id: uuid.UUID | None = None,
                vault_root: str | None = None,
                dry_run: bool = False) -> GraphifyReport:
    """Пересобрать производный граф по обоим контурам.

    Разделение то же, что у публикации и разрешения сущностей:
    health-источник читается своей ролью в своём соединении, и его
    разметка не покидает приватного дерева.
    """
    tenant_id = bind_knowledge_user(session, knowledge_user_id)
    # Тот же корень, что у записи исходников. Без явного параметра тест
    # писал бы в настоящий Vault машины разработчика — та же причина,
    # по которой он есть у `ingest.py`.
    vault_root = vault_root or DEFAULT_VAULT_ROOT
    rows = session.execute(
        select(KnowledgeSource.domain, KnowledgeSource.current_semantic_run_id)
        .where(KnowledgeSource.current_semantic_run_id.is_not(None))).all()
    public_runs = {r for d, r in rows if not is_health_domain(d)}
    health_runs = {r for d, r in rows if is_health_domain(d)}

    report = GraphifyReport(dry_run=dry_run)
    report.scopes.append(build_scope(
        session, PUBLIC_MODELS, tenant_id=tenant_id, scope="general",
        run_ids=public_runs,
        root=graphify_root(vault_root, domain="general", knowledge_user_id=tenant_id),
        markdown_root=semantic_root(vault_root, domain="general",
                                    knowledge_user_id=tenant_id),
        dry_run=dry_run))

    if health_runs and health_schema_configured():
        with health_session(tenant_id) as graph:
            report.scopes.append(build_scope(
                graph, HEALTH_MODELS, tenant_id=tenant_id, scope="health",
                run_ids=health_runs,
                root=graphify_root(vault_root, domain="health",
                                   knowledge_user_id=tenant_id),
                markdown_root=semantic_root(vault_root, domain="health",
                                            knowledge_user_id=tenant_id),
                dry_run=dry_run))
    return report


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R9: производный граф знаний")
    parser.add_argument("--dry-run", action="store_true",
                        help="посчитать, ничего не записывая на диск")
    args = parser.parse_args(argv)

    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    with sessionmaker(engine, expire_on_commit=False)() as session:
        report = rebuild_all(session, dry_run=args.dry_run)
        # Модуль только читает базу: откат вместо коммита — не
        # осторожность, а утверждение о том, что писать нечего.
        session.rollback()
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
