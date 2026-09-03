#!/usr/bin/env python3
"""R4.6.F1.2 — сборка самодостаточного `r4-f1-2-nli-benchmark-v3.sh`.

Найдено живым прогоном (run 234): `helm-knowledge-worker` — СОБРАННЫЙ
Docker-образ (`COPY helm_core` в `Dockerfile.worker`, не bind-mount с
хоста) — новый код (`relation_verbalizer_v3.py`,
`relation_benchmark_v3_fixtures.py`, `nli_relation_dataset_v3.py`)
физически отсутствует внутри контейнера, пока не случится полноценный
деплой (`action=deploy`). Деплоить агенту запрещено (CLAUDE.md §5.2).

`recon`-пайплайн `deploy.yml` переносит на VPS РОВНО ОДИН файл — сам
`.sh`. Решение: сделать recon-скрипт самодостаточным — этот генератор
СКЛЕИВАЕТ исходники трёх модулей (единственный источник истины — сами
`.py`-файлы в `control-plane/helm_core/knowledge/`, не копия здесь) в
один Python-блок внутри `.sh`, переписывая относительные импорты
`nli_relation_dataset_v3.py` на прямые ссылки на уже склеенные имена
(без реального пакета `helm_core` на этом пути). Тела трёх модулей НЕ
меняются — только удаляются их `from __future__ import annotations`/
`from . import ...` строки (дублирующиеся между файлами или
неприменимые вне пакета).

Запуск: `python3 scripts/r4-f1-2-build-recon-script.py` из
`helm/tree` — перезаписывает `scripts/r4-f1-2-nli-benchmark-v3.sh`,
сохраняя bash-обвязку (lifecycle/PRE-POST/cleanup) и заменяя только
Python-heredoc."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CP = ROOT / "control-plane" / "helm_core" / "knowledge"
SCRIPT_PATH = ROOT / "scripts" / "r4-f1-2-nli-benchmark-v3.sh"


def _strip_future_import(src: str) -> str:
    return re.sub(r"^from __future__ import annotations\n", "", src, count=1, flags=re.MULTILINE)


def _module_body(name: str) -> str:
    return _strip_future_import((CP / name).read_text(encoding="utf-8"))


def build_prelude() -> str:
    verbalizer = _module_body("relation_verbalizer_v3.py")

    fixtures = _module_body("relation_benchmark_v3_fixtures.py")

    dataset = _module_body("nli_relation_dataset_v3.py")
    dataset = dataset.replace(
        "from . import relation_verbalizer_v3 as v3\n"
        "from .relation_benchmark_v3_fixtures import RELATION_BENCHMARK_V3_CASES, RelationCaseV3\n",
        "import types as _types\n"
        "v3 = _types.SimpleNamespace(Node=Node, verbalize=verbalize, UNSUPPORTED_FOR_NLI=UNSUPPORTED_FOR_NLI)\n",
    )
    assert "from . import" not in dataset and "from .relation_benchmark" not in dataset, (
        "relative import rewrite failed — nli_relation_dataset_v3.py содержимое изменилось, "
        "обнови паттерн замены в этом генераторе")

    return (
        "# ---- склеено из helm_core/knowledge/relation_verbalizer_v3.py "
        "(единственный источник истины — см. control-plane/, не копия здесь) ----\n"
        + verbalizer
        + "\n# ---- склеено из helm_core/knowledge/relation_benchmark_v3_fixtures.py ----\n"
        + fixtures
        + "\n# ---- склеено из helm_core/knowledge/nli_relation_dataset_v3.py "
        "(относительные импорты переписаны на прямые ссылки, пакета helm_core здесь нет) ----\n"
        + dataset
    )


def main() -> int:
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    marker_start = "from helm_core.knowledge.nli_relation_dataset_v3 import build_examples_v3\n"
    if marker_start not in text:
        print("::error::маркер импорта не найден в текущем .sh — структура файла изменилась")
        return 1
    prelude = build_prelude()
    new_text = text.replace(marker_start, prelude, 1)
    SCRIPT_PATH.write_text(new_text, encoding="utf-8")
    print(f"Собрано: {SCRIPT_PATH} ({len(new_text)} байт, было {len(text)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
