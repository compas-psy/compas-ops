#!/usr/bin/env python3
"""graph_index.py — регенерирует INDEX.md из graph/ops/graph.json
(CLAUDE.md §5.7: «указатель, отставший от графа, хуже отсутствующего»).

    python3 tools/graph_index.py graph/ops/graph.json graph/INDEX.md
    python3 tools/graph_index.py graph/ops/graph.json graph/helm/INDEX.md --scope helm/

Без --scope индекс охватывает весь репозиторий (документы compas-ops).
С --scope <префикс> — только узлы, чей файл начинается с этого префикса
(код/документы одного продукта, напр. `helm/`).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TYPE_LABEL = {
    "adr": "ADR",
    "spec_section": "§-раздел спеки",
    "roadmap_item": "пункт роадмапа (P8.x)",
    "finding": "находка (F-YYMMDD-NN)",
}
TYPE_ORDER = ["adr", "spec_section", "roadmap_item", "finding"]
MAX_FILES_SHOWN = 6


def build_index(graph: dict, scope: str | None) -> str:
    nodes_by_id = {n["id"]: n for n in graph["nodes"]}

    # файл входит в область видимости индекса, если scope не задан или
    # путь файла начинается с него
    def file_in_scope(path: str) -> bool:
        return scope is None or path.startswith(scope)

    edges_by_ident: dict[str, list[dict]] = {}
    for e in graph["edges"]:
        file_node = nodes_by_id.get(e["from"])
        if file_node is None or file_node["type"] != "file":
            continue
        if not file_in_scope(file_node["path"]):
            continue
        edges_by_ident.setdefault(e["to"], []).append(e)

    lines = []
    if scope is None:
        title = "graph/INDEX.md — указатель по документам compas-ops"
        rebuild_cmd = "python3 tools/graph_index.py graph/ops/graph.json graph/INDEX.md"
    else:
        product = scope.rstrip("/")
        title = f"graph/{product}/INDEX.md — указатель по коду и документам продукта"
        rebuild_cmd = (f"python3 tools/graph_index.py graph/ops/graph.json "
                        f"graph/{product}/INDEX.md --scope {scope}")
    lines.append(f"# {title}\n")
    lines.append(
        "Сгенерировано `tools/graph_index.py` из `graph/ops/graph.json` — "
        "не редактировать руками. Пересборка:\n"
        "```\n"
        "python3 tools/graphify.py build\n"
        f"{rebuild_cmd}\n"
        "```\n"
    )

    total_idents = sum(1 for k in edges_by_ident if nodes_by_id[k]["type"] != "file")
    lines.append(f"Тем в индексе: {total_idents}. Полные связи и провенанс — "
                  f"`graphify explain \"<id>\" --graph graph/ops/graph.json`.\n")

    for kind in TYPE_ORDER:
        idents = [nodes_by_id[i] for i in edges_by_ident if nodes_by_id[i]["type"] == kind]
        if not idents:
            continue
        idents.sort(key=lambda n: n["value"])
        lines.append(f"## {TYPE_LABEL[kind]}\n")
        for n in idents:
            files = sorted({e["file"] for e in edges_by_ident[n["id"]]})
            shown = files[:MAX_FILES_SHOWN]
            rest = len(files) - len(shown)
            file_list = ", ".join(f"`{f}`" for f in shown)
            if rest > 0:
                file_list += f" (+{rest})"
            lines.append(f"- `{n['label']}` — {file_list}")
        lines.append("")

    if scope is None:
        # Ссылки на продуктовые индексы — только если такие уже собраны.
        product_indexes = sorted(Path("graph").glob("*/INDEX.md"))
        if product_indexes:
            lines.append("## Индексы продуктов\n")
            for p in product_indexes:
                lines.append(f"- `{p.as_posix()}`")
            lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("graph_json", type=Path)
    parser.add_argument("output_md", type=Path)
    parser.add_argument("--scope", default=None, help="ограничить индекс файлами с этим префиксом пути")
    args = parser.parse_args()

    if not args.graph_json.exists():
        sys.exit(f"{args.graph_json} не найден — сначала `python3 tools/graphify.py build`")

    graph = json.loads(args.graph_json.read_text(encoding="utf-8"))
    content = build_index(graph, args.scope)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(content, encoding="utf-8")
    print(f"{args.output_md}: готово")


if __name__ == "__main__":
    main()
