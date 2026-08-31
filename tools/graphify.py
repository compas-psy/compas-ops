#!/usr/bin/env python3
"""graphify — детерминированный граф связей compas-ops (CLAUDE.md §5.7).

Строит graph/ops/graph.json из совпадения идентификаторов (ADR-NNN,
§N.N, P8.N.N, F-YYMMDD-NN) в документах и коде продуктов. Узлы — файлы
и идентификаторы; рёбра — только "relates_to" (совместное упоминание в
файле не означает causes/supports/blocks и т.п. — тот же принцип, что
E13 применяет к [[wikilink]]: явно не указан тип связи → relates_to, не
додумывать семантику). Каждое ребро несёт provenance (файл + строка).

Команды:
    graphify.py build                          — пересобрать graph.json
    graphify.py explain "<понятие>"             — связи одного узла
    graphify.py path "<A>" "<Б>"                — кратчайший путь A → Б
    graphify.py affected "<что трогаешь>"       — что упоминает узел
    graphify.py benchmark                       — оценка стоимости в токенах

Использует только stdlib — отдельной установки не требует.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH_JSON = ROOT / "graph" / "ops" / "graph.json"
GRAPH_HTML = ROOT / "graph" / "ops" / "graph.html"
STALE_MARKER = ROOT / "graph" / "ops" / "STALE.md"

SCAN_EXTS = {".md", ".py", ".yml", ".yaml", ".sh"}
# "graph" исключён: там лежат СГЕНЕРИРОВАННЫЕ артефакты (graph.json,
# INDEX.md, graph.html) — индексировать индекс как источник значило бы,
# что каждая пересборка сама себя делает устаревшей (правка INDEX.md
# меняет corpus_hash уже после того, как graph.json его зафиксировал).
PRUNE_DIRS = {".git", "node_modules", "__pycache__", "dist", ".runner", "graph"}

# Идентификатор -> (тип узла, human-readable префикс). Порядок важен:
# более специфичный P8\. должен идти раньше на случай пересечений.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("adr", re.compile(r"\bADR-(\d{3})\b")),
    ("spec_section", re.compile(r"§(\d+(?:\.\d+)+)")),
    ("roadmap_item", re.compile(r"\bP8\.(\d+(?:\.\d+)?)\b")),
    ("finding", re.compile(r"\bF-(\d{6}-\d{2})\b")),
]

TYPE_LABEL = {
    "adr": "ADR",
    "spec_section": "§-раздел спеки",
    "roadmap_item": "пункт роадмапа (P8.x)",
    "finding": "находка (F-YYMMDD-NN)",
    "file": "файл",
}


def iter_scan_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in SCAN_EXTS:
            continue
        if any(part in PRUNE_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def file_title(path: Path, text: str) -> str:
    if path.suffix == ".md":
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#"):
                return line.lstrip("#").strip()
    return path.name


def node_id_for(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def corpus_hash(root: Path = ROOT) -> str:
    """Хэш содержимого всего просканированного корпуса — основа проверки
    свежести (`graphify check-stale`): изменился хоть один просканированный
    файл → хэш другой → граф устарел."""
    digest = hashlib.sha256()
    for path in iter_scan_files(root):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            pass
    return digest.hexdigest()


def build_graph(root: Path = ROOT) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    seen_edges: set[tuple[str, str, int]] = set()

    for path in iter_scan_files(root):
        rel = str(path.relative_to(root))
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        file_node_id = node_id_for("file", rel)
        if file_node_id not in nodes:
            nodes[file_node_id] = {
                "id": file_node_id,
                "type": "file",
                "path": rel,
                "title": file_title(path, text),
            }

        lines = text.splitlines()
        for line_no, line in enumerate(lines, start=1):
            for kind, pattern in PATTERNS:
                for m in pattern.finditer(line):
                    value = m.group(1)
                    ident_id = node_id_for(kind, value)
                    if ident_id not in nodes:
                        nodes[ident_id] = {
                            "id": ident_id,
                            "type": kind,
                            "value": value,
                            "label": m.group(0),
                        }
                    key = (file_node_id, ident_id, line_no)
                    if key in seen_edges:
                        continue
                    seen_edges.add(key)
                    edges.append({
                        "from": file_node_id,
                        "to": ident_id,
                        "relation_type": "relates_to",
                        "evidence_type": "explicit_link",
                        "file": rel,
                        "line": line_no,
                    })

    return {
        "generated_by": "tools/graphify.py",
        "corpus_hash": corpus_hash(root),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": list(nodes.values()),
        "edges": edges,
    }


def load_graph(graph_path: Path) -> dict:
    if not graph_path.exists():
        sys.exit(f"{graph_path} не найден — сначала `python3 tools/graphify.py build`")
    return json.loads(graph_path.read_text(encoding="utf-8"))


def adjacency(graph: dict) -> dict[str, list[dict]]:
    adj: dict[str, list[dict]] = {}
    for e in graph["edges"]:
        adj.setdefault(e["from"], []).append(e)
        adj.setdefault(e["to"], []).append(e)
    return adj


def resolve_node(graph: dict, query: str) -> list[dict]:
    """Найти узлы по неточному имени. Возвращает список кандидатов."""
    q = query.strip().lstrip("§").strip()
    q_lower = q.lower()
    exact: list[dict] = []
    partial: list[dict] = []
    for n in graph["nodes"]:
        haystacks = [n["id"], n.get("value", ""), n.get("label", ""),
                     n.get("path", ""), n.get("title", "")]
        haystacks_lower = [h.lower() for h in haystacks if h]
        if q_lower in haystacks_lower or q in haystacks:
            exact.append(n)
        elif any(q_lower in h for h in haystacks_lower):
            partial.append(n)
    return exact or partial


def cmd_build(_args) -> None:
    graph = build_graph()
    GRAPH_JSON.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_JSON.write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
    write_html(graph)
    STALE_MARKER.unlink(missing_ok=True)
    print(f"graph.json: {graph['node_count']} узлов, {graph['edge_count']} рёбер → {GRAPH_JSON}")
    print(f"graph.html → {GRAPH_HTML}")
    print("Не забудьте пересобрать индексы: python3 tools/graph_index.py graph/ops/graph.json graph/INDEX.md")


def cmd_check_stale(_args) -> None:
    graph = load_graph(GRAPH_JSON)
    current = corpus_hash()
    if graph.get("corpus_hash") == current:
        STALE_MARKER.unlink(missing_ok=True)
        print("Граф свежий.")
        return
    STALE_MARKER.parent.mkdir(parents=True, exist_ok=True)
    STALE_MARKER.write_text(
        "# STALE\n\nСодержимое документов/кода изменилось после последней "
        "сборки графа. Работайте по файлам напрямую, не по графу, пока не "
        "пересоберёте: `python3 tools/graphify.py build && "
        "python3 tools/graph_index.py graph/ops/graph.json graph/INDEX.md`.\n",
        encoding="utf-8",
    )
    print(f"Граф устарел — записано {STALE_MARKER}")


def _describe(n: dict) -> str:
    if n["type"] == "file":
        return f"{n['id']} ({n['title']})"
    return f"{n['id']} [{TYPE_LABEL.get(n['type'], n['type'])}]"


def cmd_explain(args) -> None:
    graph = load_graph(args.graph)
    candidates = resolve_node(graph, args.concept)
    if not candidates:
        print(f"Ничего не найдено по «{args.concept}».")
        return
    if len(candidates) > 1:
        print(f"Неточно — {len(candidates)} кандидатов, повторите с id:")
        for n in candidates[:30]:
            print(f"  {_describe(n)}")
        if len(candidates) > 30:
            print(f"  ...и ещё {len(candidates) - 30}")
        return
    node = candidates[0]
    adj = adjacency(graph)
    neighbors = adj.get(node["id"], [])
    print(_describe(node))
    if not neighbors:
        print("  (нет связей в графе)")
        return
    print(f"  {len(neighbors)} связей (relates_to, явное совпадение по тексту):")
    for e in sorted(neighbors, key=lambda e: (e["file"], e["line"])):
        other_id = e["to"] if e["from"] == node["id"] else e["from"]
        other = next(x for x in graph["nodes"] if x["id"] == other_id)
        print(f"  {e['file']}:{e['line']} → {_describe(other)}")


def cmd_path(args) -> None:
    graph = load_graph(args.graph)
    starts = resolve_node(graph, args.a)
    ends = resolve_node(graph, args.b)
    for label, matches, raw in (("A", starts, args.a), ("Б", ends, args.b)):
        if len(matches) != 1:
            print(f"«{raw}» неоднозначно или не найдено ({len(matches)} кандидатов):")
            for n in matches[:30]:
                print(f"  {_describe(n)}")
            return
    start, end = starts[0]["id"], ends[0]["id"]
    adj = adjacency(graph)

    prev: dict[str, str] = {start: None}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        if cur == end:
            break
        for e in adj.get(cur, []):
            nxt = e["to"] if e["from"] == cur else e["from"]
            if nxt not in prev:
                prev[nxt] = cur
                queue.append(nxt)

    if end not in prev:
        print(f"Пути между {starts[0]['id']} и {ends[0]['id']} нет.")
        return

    path = [end]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()

    by_id = {n["id"]: n for n in graph["nodes"]}
    print(" → ".join(_describe(by_id[nid]) for nid in path))


def cmd_affected(args) -> None:
    cmd_explain(args)


def cmd_benchmark(args) -> None:
    graph = load_graph(args.graph)
    index_files = [ROOT / "graph" / "INDEX.md", ROOT / "graph" / "helm" / "INDEX.md"]
    index_chars = sum(p.read_text(encoding="utf-8").__len__() for p in index_files if p.exists())
    corpus_chars = sum(len(p.read_text(encoding="utf-8", errors="ignore")) for p in iter_scan_files(ROOT))
    # Грубая оценка ~4 символа/токен — тот же порядок величины, которым
    # обычно прикидывают токены без реального токенизатора.
    index_tokens = index_chars // 4
    corpus_tokens = corpus_chars // 4
    print(f"graph/INDEX.md + graph/helm/INDEX.md: ~{index_tokens} токенов")
    print(f"весь просканированный корпус ({graph['node_count']} узлов, "
          f"{sum(1 for n in graph['nodes'] if n['type'] == 'file')} файлов): ~{corpus_tokens} токенов")
    if index_tokens:
        print(f"дешевле в ~{corpus_tokens / index_tokens:.1f} раз (для ЭТОГО репозитория, не переиспользованная цифра)")
    else:
        print("индексы ещё не собраны — python3 tools/graph_index.py")


def write_html(graph: dict) -> None:
    payload = json.dumps(graph, ensure_ascii=False)
    html = HTML_TEMPLATE.replace("__GRAPH_JSON__", payload)
    GRAPH_HTML.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_HTML.write_text(html, encoding="utf-8")


HTML_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>compas-ops · graphify</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
  body { margin:0; font-family: system-ui, sans-serif; background:#0b0d10; color:#e6e6e6; }
  #bar { padding:8px 12px; background:#14171b; display:flex; gap:8px; align-items:center; }
  #bar input { flex:1; padding:6px 8px; border-radius:4px; border:1px solid #333; background:#0b0d10; color:#eee; }
  #net { width:100%; height:calc(100vh - 46px); }
  #info { position:absolute; right:12px; top:56px; max-width:340px; background:#14171bcc; padding:10px 12px; border-radius:6px; font-size:13px; display:none; }
</style>
</head>
<body>
<div id="bar">
  <strong>graphify</strong>
  <input id="filter" placeholder="фильтр по id/файлу/§/ADR/P8.x/F-...">
</div>
<div id="net"></div>
<div id="info"></div>
<script>
const GRAPH = __GRAPH_JSON__;
const COLORS = { file: "#4c8bf5", adr: "#f5a623", spec_section: "#7ed321", roadmap_item: "#bd10e0", finding: "#e0245e" };
function toVis(graph) {
  const nodes = graph.nodes.map(n => ({
    id: n.id,
    label: n.type === "file" ? n.title : n.label,
    title: n.type === "file" ? n.path : n.id,
    color: COLORS[n.type] || "#888",
    shape: n.type === "file" ? "dot" : "diamond",
    size: n.type === "file" ? 8 : 6,
  }));
  const edges = graph.edges.map(e => ({ from: e.from, to: e.to, title: e.file + ":" + e.line, color: { color: "#333" } }));
  return { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) };
}
const data = toVis(GRAPH);
const network = new vis.Network(document.getElementById("net"), data, {
  physics: { stabilization: { iterations: 150 }, barnesHut: { gravitationalConstant: -4000 } },
  interaction: { hover: true },
});
const info = document.getElementById("info");
network.on("selectNode", params => {
  const id = params.nodes[0];
  const node = GRAPH.nodes.find(n => n.id === id);
  const edges = GRAPH.edges.filter(e => e.from === id || e.to === id);
  info.style.display = "block";
  info.innerHTML = "<strong>" + id + "</strong><br>" + edges.length + " связей<br>" +
    edges.slice(0, 20).map(e => e.file + ":" + e.line).join("<br>");
});
document.getElementById("filter").addEventListener("input", ev => {
  const q = ev.target.value.toLowerCase();
  if (!q) { data.nodes.forEach(n => data.nodes.update({ id: n.id, hidden: false })); return; }
  GRAPH.nodes.forEach(n => {
    const hay = (n.id + " " + (n.path || "") + " " + (n.title || "") + " " + (n.label || "")).toLowerCase();
    data.nodes.update({ id: n.id, hidden: !hay.includes(q) });
  });
});
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("build", help="пересобрать graph/ops/graph.json и graph.html").set_defaults(func=cmd_build)
    sub.add_parser("check-stale", help="сверить corpus_hash графа с текущими файлами, пишет/убирает graph/ops/STALE.md").set_defaults(func=cmd_check_stale)

    p = sub.add_parser("explain", help="связи одного узла")
    p.add_argument("concept")
    p.add_argument("--graph", type=Path, default=GRAPH_JSON)
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("path", help="кратчайший путь между двумя узлами")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--graph", type=Path, default=GRAPH_JSON)
    p.set_defaults(func=cmd_path)

    p = sub.add_parser("affected", help="что упоминает узел (алиас explain)")
    p.add_argument("concept")
    p.add_argument("--graph", type=Path, default=GRAPH_JSON)
    p.set_defaults(func=cmd_affected)

    p = sub.add_parser("benchmark", help="оценка стоимости в токенах: индекс vs весь корпус")
    p.add_argument("--graph", type=Path, default=GRAPH_JSON)
    p.set_defaults(func=cmd_benchmark)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.stderr.close()
