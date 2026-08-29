#!/usr/bin/env python3
"""Экспорт и восстановление workflow n8n (ТЗ §17.5).

Разворачивается на сервере как /opt/helm/scripts/n8n-workflows.py.

`export` вызывается ночью из backup.sh (§23: «23:30 daily backup + n8n
export»), `restore` — руками, когда n8n потерял workflow. Значения
credentials через публичный API n8n не отдаются вовсе, поэтому «secret-free»
здесь не фильтр, а свойство источника: в выгрузке остаются только ссылки на
credentials по id, сами секреты живут в БД n8n, зашифрованные
N8N_ENCRYPTION_KEY (он в бэкапе вместе со всем /etc/helm/secrets).

Файлы кладутся по одному на workflow и нормализуются: ключи сортируются,
изменчивые поля (updatedAt, versionId и подобные) выбрасываются. Без этого
любой git diff показывал бы изменения там, где ничего не менялось, и
экспорт перестал бы быть полезным как история.

Запуск:
    sudo python3 /opt/helm/scripts/n8n-workflows.py export
    sudo python3 /opt/helm/scripts/n8n-workflows.py restore [ИМЯ_ФАЙЛА ...]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "http://127.0.0.1:5678/api/v1"
API_KEY_FILE = Path("/etc/helm/secrets/n8n_api_key")
EXPORT_DIR = Path("/opt/helm/n8n/exports")
REQUEST_TIMEOUT = 30

#: Поля, меняющиеся сами по себе при каждом сохранении и не несущие смысла
#: для восстановления. Оставить их — значит получать шум в каждом diff.
VOLATILE_FIELDS = {"updatedAt", "createdAt", "versionId", "triggerCount",
                   "shared", "homeProject", "scopes"}

#: Поля, которые API не принимает обратно при создании workflow.
NOT_ACCEPTED_ON_CREATE = {"id", "active", "tags", "pinData", "meta"}


def api(path: str, key: str, *, method: str = "GET",
        body: dict | None = None) -> dict:
    request = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={"X-N8N-API-KEY": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        # Тело ответа несёт причину (какое поле не принято) — без него
        # остаётся только код статуса и диагностика вслепую.
        sys.exit(f"n8n API {method} {path}: HTTP {exc.code} "
                 f"{exc.read().decode(errors='replace')}")
    except urllib.error.URLError as exc:
        sys.exit(f"n8n недоступен по {API_BASE}: {exc.reason}")


def read_key() -> str:
    if not API_KEY_FILE.exists():
        sys.exit(f"нет {API_KEY_FILE} — создай ключ в n8n (Settings → n8n API) "
                 "и положи его туда: см. scripts/n8n-export-runbook.md")
    return API_KEY_FILE.read_text(encoding="utf-8").strip()


def normalize(workflow: dict) -> dict:
    return {k: v for k, v in sorted(workflow.items()) if k not in VOLATILE_FIELDS}


def safe_filename(workflow: dict) -> str:
    """Имя файла — по имени workflow, а не по id.

    id генерируется n8n и после восстановления меняется; имя — то, чем
    workflow называет владелец, и по нему файл узнаётся в git-истории.
    Оба поля сохраняются внутри файла, здесь только выбор имени.
    """
    name = re.sub(r"[^\w\-]+", "-", workflow.get("name", "unnamed"), flags=re.UNICODE)
    return f"{name.strip('-').lower() or 'unnamed'}.json"


def do_export(key: str) -> int:
    workflows = api("/workflows?limit=250", key).get("data", [])
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    written = set()
    for workflow in workflows:
        # Список отдаёт workflow без nodes/connections — за содержимым
        # нужен отдельный запрос по id, иначе выгрузка окажется пустой
        # оболочкой, на которой restore ничего не восстановит.
        full = api(f"/workflows/{workflow['id']}", key)
        path = EXPORT_DIR / safe_filename(full)
        path.write_text(json.dumps(normalize(full), ensure_ascii=False, indent=2,
                                   sort_keys=True) + "\n", encoding="utf-8")
        written.add(path.name)
        print(f"выгружен: {path.name}")

    # Удалённый в n8n workflow должен исчезнуть и из выгрузки — иначе
    # restore однажды воскресит то, что владелец сознательно убрал.
    for stale in EXPORT_DIR.glob("*.json"):
        if stale.name not in written:
            stale.unlink()
            print(f"удалён из выгрузки (нет в n8n): {stale.name}")

    print(f"всего workflow: {len(workflows)} → {EXPORT_DIR}")
    return 0


def do_restore(key: str, names: list[str]) -> int:
    files = [EXPORT_DIR / n for n in names] if names else sorted(EXPORT_DIR.glob("*.json"))
    if not files:
        sys.exit(f"в {EXPORT_DIR} нет файлов для восстановления")

    existing = {w["name"] for w in api("/workflows?limit=250", key).get("data", [])}
    for path in files:
        if not path.exists():
            sys.exit(f"файл не найден: {path}")
        workflow = json.loads(path.read_text(encoding="utf-8"))
        if workflow.get("name") in existing:
            # Перезапись существующего workflow не делается молча: он мог
            # быть изменён после экспорта, и восстановление затёрло бы
            # правку. Владелец переименовывает или удаляет сам.
            print(f"ПРОПУЩЕН (уже есть в n8n): {workflow.get('name')!r}")
            continue
        payload = {k: v for k, v in workflow.items() if k not in NOT_ACCEPTED_ON_CREATE}
        created = api("/workflows", key, method="POST", body=payload)
        print(f"восстановлен: {workflow.get('name')!r} → id {created.get('id')}")

    print("Восстановленные workflow неактивны — включать по одному вручную "
          "после проверки (§17.6: активация после fixture-теста).")
    return 0


def main() -> None:
    if os.geteuid() != 0:
        sys.exit("требуется root: секрет ключа API читается только им")

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("export", help="выгрузить все workflow в /opt/helm/n8n/exports")
    restore = sub.add_parser("restore", help="восстановить workflow из выгрузки")
    restore.add_argument("names", nargs="*", help="имена файлов; без аргументов — все")
    args = parser.parse_args()

    key = read_key()
    sys.exit(do_export(key) if args.command == "export" else do_restore(key, args.names))


if __name__ == "__main__":
    main()
