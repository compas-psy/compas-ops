#!/usr/bin/env python3
"""Миграция репозиториев GitHub → Forgejo по чек-листу ТЗ §18.3.

Разворачивается на сервере как /opt/helm/scripts/forgejo-migrate.py.
Требует root (читает /etc/helm/secrets/github_mirror_pat, 600 root:root —
категория host-side секретов, как restic_password, см. F-260829-09).

Делает для каждого репозитория (шаги §18.3, кроме 10-11 — см. ниже):
  1.  inventory: refs GitHub (heads+tags), открытые PR, workflow-триггеры, LFS;
  2-5. мирация через встроенный Forgejo migrate API (клонирует полный набор
      refs сам, внутри контейнера) в приватный репозиторий организации
      compas-psy — самодельный clone+push не пишем (§18.4);
  3/6. integrity check: git ls-remote GitHub против ls-remote по диску
      Forgejo — полное совпадение SHA всех heads и tags, иначе стоп;
  7-8. built-in push mirror Forgejo → GitHub, sync_on_commit включён;
  9.  немедленный push_mirrors-sync + повторное сравнение refs — «prove
      mirrored exact SHA».
Шаг 10 (prove CI) и шаг 11 (переключение primary remote) сознательно НЕ
здесь: CI-проба — это реальный push через рабочий цикл, переключение
remote — отдельное решение владельца после PASS (§18.3: «only then»).

compas-ops в списке по умолчанию НЕТ: на нём идёт активная разработка,
а push mirror перезаписывает refs на GitHub стороной Forgejo — миграция
compas-ops выполняется отдельным запуском (аргументом) после merge
текущей рабочей ветки. Иначе mirror-sync затрёт свежие GitHub-коммиты
состоянием Forgejo на момент миграции.

helm-infra на GitHub не существует (проверено 29.08.2026) — по ТЗ P6.5 это
опциональный импорт «for future history», сюда не входит.

Запуск:  sudo python3 /opt/helm/scripts/forgejo-migrate.py [имя_репо ...]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GITHUB_ORG = "compas-psy"
FORGEJO_ORG = "compas-psy"
FORGEJO_ADMIN = "ILYA"
FORGEJO_CONTAINER = "helm-forgejo-1"
FORGEJO_API = "http://127.0.0.1:3000/api/v1"
PAT_FILE = Path("/etc/helm/secrets/github_mirror_pat")
LOG_FILE = Path("/var/log/helm/forgejo-migration.log")

# §18.2 минус compas-ops (активная разработка — мигрируется отдельным
# запуском после merge) и минус helm-infra (не существует на GitHub).
REPOS_DEFAULT = ["compas-voice", "cmpas.ru", "zapiski", "signalAI-mobileApp"]

report_lines: list[str] = []


def say(line: str) -> None:
    print(line, flush=True)
    report_lines.append(line)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def forgejo_api(method: str, path: str, token: str, body: dict | None = None,
                timeout: int = 600) -> tuple[int, dict | list | None]:
    """Вызов Forgejo API. Возвращает (status, разобранный JSON или None)."""
    req = urllib.request.Request(
        FORGEJO_API + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"token {token}",
                 "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return e.code, {"raw": raw.decode(errors="replace")}


def github_refs(repo: str) -> list[str] | None:
    """heads+tags репозитория на GitHub. Репозитории публичные — без auth.

    git запускается внутри контейнера Forgejo: на хосте git может быть
    не установлен (деплой scp-based, /opt/helm — не git-репозиторий).
    """
    result = run(["docker", "exec", "-u", "git", FORGEJO_CONTAINER,
                  "git", "ls-remote", "--heads", "--tags",
                  f"https://github.com/{GITHUB_ORG}/{repo}.git"])
    if result.returncode != 0:
        say(f"    ОШИБКА ls-remote GitHub: {result.stderr.strip()}")
        return None
    return sorted(line for line in result.stdout.splitlines() if line.strip())


def forgejo_refs(repo: str) -> list[str] | None:
    """heads+tags уже мигрированного репозитория — по диску, без сети/auth.

    Forgejo хранит bare-репозитории под именами в нижнем регистре.
    """
    bare = f"/data/git/repositories/{FORGEJO_ORG.lower()}/{repo.lower()}.git"
    result = run(["docker", "exec", "-u", "git", FORGEJO_CONTAINER,
                  "git", "ls-remote", "--heads", "--tags", bare])
    if result.returncode != 0:
        say(f"    ОШИБКА ls-remote Forgejo ({bare}): {result.stderr.strip()}")
        return None
    return sorted(line for line in result.stdout.splitlines() if line.strip())


def bare_git(repo: str, *args: str) -> subprocess.CompletedProcess:
    bare = f"/data/git/repositories/{FORGEJO_ORG.lower()}/{repo.lower()}.git"
    return run(["docker", "exec", "-u", "git", FORGEJO_CONTAINER,
                "git", "--git-dir", bare, *args])


def inventory_open_prs(repo: str) -> None:
    """Шаг 1 §18.3: открытые PR. Публичный репозиторий — API без auth."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/pulls?state=open",
        headers={"User-Agent": "helm-forgejo-migrate"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            prs = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001 — инвентаризация, не гейт
        say(f"    открытые PR: не удалось проверить ({e}) — проверь вручную")
        return
    if not prs:
        say("    открытых PR нет")
        return
    say(f"    ВНИМАНИЕ: {len(prs)} открытых PR — по §18.3 завершить в GitHub "
        "до cutover или пересоздать в Forgejo:")
    for pr in prs:
        say(f"      #{pr['number']} {pr['title']!r} {pr['html_url']}")


def inventory_workflows_and_lfs(repo: str) -> None:
    """Шаг 1/10 §18.3: какие workflow есть и на что триггерятся; LFS."""
    ls = bare_git(repo, "ls-tree", "-r", "--name-only", "HEAD",
                  ".github/workflows")
    files = [f for f in ls.stdout.splitlines() if f.strip()] \
        if ls.returncode == 0 else []
    if not files:
        say("    workflow-файлов нет — шаг «prove CI» для этого репо пуст")
    for f in files:
        content = bare_git(repo, "show", f"HEAD:{f}").stdout
        triggers = [t for t in ("push", "pull_request", "workflow_dispatch",
                                "schedule") if t in content]
        say(f"    workflow {f}: упоминает триггеры {triggers or ['—']} "
            "(грубая проверка по подстроке — перед prove-CI открыть файл)")

    attrs = bare_git(repo, "show", "HEAD:.gitattributes")
    if attrs.returncode == 0 and "filter=lfs" in attrs.stdout:
        say("    ВНИМАНИЕ: .gitattributes содержит filter=lfs — LFS-объекты "
            "этим скриптом НЕ мигрируются, нужен отдельный проход")


def ensure_org(token: str) -> bool:
    status, _ = forgejo_api("GET", f"/orgs/{FORGEJO_ORG}", token, timeout=30)
    if status == 200:
        return True
    status, body = forgejo_api("POST", "/orgs", token,
                               {"username": FORGEJO_ORG,
                                "visibility": "private"}, timeout=30)
    if status == 201:
        say(f"Организация {FORGEJO_ORG} создана (private)")
        return True
    say(f"ОШИБКА создания организации: HTTP {status} {body}")
    return False


def migrate_repo(repo: str, token: str, pat: str) -> bool:
    say(f"\n=== {repo} ===")

    say("  [1] inventory")
    gh_before = github_refs(repo)
    if gh_before is None:
        return False
    say(f"    refs на GitHub: {len(gh_before)} (heads+tags)")
    inventory_open_prs(repo)

    say("  [2-5] migrate → Forgejo")
    status, body = forgejo_api("POST", "/repos/migrate", token, {
        "clone_addr": f"https://github.com/{GITHUB_ORG}/{repo}.git",
        "repo_name": repo,
        "repo_owner": FORGEJO_ORG,
        "service": "git",
        "private": True,
        "mirror": False,
    })
    if status == 201:
        say("    мигрирован")
    elif status == 409:
        say("    уже существует в Forgejo — только проверка и mirror")
    else:
        say(f"    ОШИБКА миграции: HTTP {status} {body}")
        return False

    say("  [3/6] integrity: refs GitHub == refs Forgejo")
    fj = forgejo_refs(repo)
    if fj is None:
        return False
    if fj != gh_before:
        say(f"    РАСХОЖДЕНИЕ ({len(gh_before)} GitHub vs {len(fj)} Forgejo) "
            "— push mirror НЕ настраивается, разобраться вручную:")
        for line in sorted(set(gh_before) ^ set(fj)):
            say(f"      {line}")
        return False
    say(f"    совпадают, {len(fj)} refs")

    inventory_workflows_and_lfs(repo)

    say("  [7-8] push mirror → GitHub, sync_on_commit")
    remote = f"https://github.com/{GITHUB_ORG}/{repo}.git"
    status, mirrors = forgejo_api(
        "GET", f"/repos/{FORGEJO_ORG}/{repo}/push_mirrors", token, timeout=30)
    if status == 200 and any(m.get("remote_address") == remote
                             for m in (mirrors or [])):
        say("    уже настроен")
    else:
        status, body = forgejo_api(
            "POST", f"/repos/{FORGEJO_ORG}/{repo}/push_mirrors", token, {
                "remote_address": remote,
                "remote_username": "x-access-token",
                "remote_password": pat,
                "interval": "8h0m0s",
                "sync_on_commit": True,
            }, timeout=30)
        if status != 200 and status != 201:
            say(f"    ОШИБКА настройки mirror: HTTP {status} {body}")
            return False
        say("    настроен (интервал 8h + sync_on_commit)")

    say("  [9] немедленный sync + prove mirrored exact SHA")
    status, body = forgejo_api(
        "POST", f"/repos/{FORGEJO_ORG}/{repo}/push_mirrors-sync", token,
        timeout=30)
    if status not in (200, 204):
        say(f"    ОШИБКА запуска sync: HTTP {status} {body}")
        return False
    synced = False
    for _ in range(12):
        time.sleep(10)
        status, mirrors = forgejo_api(
            "GET", f"/repos/{FORGEJO_ORG}/{repo}/push_mirrors", token,
            timeout=30)
        if status != 200:
            continue
        m = next((m for m in (mirrors or [])
                  if m.get("remote_address") == remote), None)
        if m is None:
            continue
        if m.get("last_error"):
            say(f"    ОШИБКА sync: {m['last_error']}")
            return False
        # у свежесозданного mirror last_update — нулевая дата 0001-01-01,
        # непустая строка: сама по себе она успехом не считается
        last_update = m.get("last_update") or ""
        if last_update and not last_update.startswith("0001-"):
            synced = True
            break
    if not synced:
        say("    sync не подтвердился за 120с — проверить в UI "
            "(Settings → Mirror) и повторить запуск")
        return False
    gh_after = github_refs(repo)
    if gh_after != fj:
        say("    РАСХОЖДЕНИЕ после sync — mirrored SHA не exact:")
        for line in sorted(set(gh_after or []) ^ set(fj)):
            say(f"      {line}")
        return False
    say(f"    GitHub после sync == Forgejo, {len(fj)} refs — exact SHA")
    say(f"  {repo}: PASS (шаги 1-9). Осталось: prove CI (шаг 10), "
        "переключение remote (шаг 11) — отдельно, решением владельца")
    return True


def main() -> None:
    if os.geteuid() != 0:
        sys.exit("требуется root: sudo python3 /opt/helm/scripts/forgejo-migrate.py")
    if not PAT_FILE.exists():
        sys.exit(f"нет {PAT_FILE} — положи fine-grained PAT (Contents: Read "
                 "and write, только выбранные репо) через sudo tee, 600 root:root")
    pat = PAT_FILE.read_text(encoding="utf-8").strip()

    repos = sys.argv[1:] or REPOS_DEFAULT
    say(f"Миграция {datetime.now(timezone.utc).isoformat()}: {', '.join(repos)}")

    token_name = f"migrate-{int(time.time())}"
    gen = run(["docker", "exec", "-u", "git", FORGEJO_CONTAINER,
               "forgejo", "admin", "user", "generate-access-token",
               "--username", FORGEJO_ADMIN, "--token-name", token_name,
               "--scopes", "write:repository,write:organization", "--raw"])
    if gen.returncode != 0:
        sys.exit(f"не удалось выпустить Forgejo-токен:\n{gen.stderr}")
    token = gen.stdout.strip().splitlines()[-1]

    ok = ensure_org(token) and all(
        [migrate_repo(r, token, pat) for r in repos])

    say(f"\nИтог: {'все PASS' if ok else 'есть ОШИБКИ — см. выше'}")
    say(f"Forgejo-токен {token_name} остаётся активным (отозвать: "
        f"git.cmpas.ru → Settings → Applications, если больше не нужен)")

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n\n")
    LOG_FILE.chmod(0o600)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
