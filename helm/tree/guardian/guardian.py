#!/usr/bin/env python3
"""helm-guardian — независимый host-level watchdog (ТЗ §25).

Независимость — не пожелание, а требование §30.10: Guardian обязан остаться
живым и записать алерт, когда остановлены Docker, Hermes, n8n, Control Plane
и Postgres. Поэтому:

- только стандартная библиотека. Никаких SQLAlchemy, requests, docker-sdk:
  зависимость, которую нельзя импортировать, — это Guardian, который не
  запустился в тот единственный момент, когда был нужен;
- Postgres пишется, только если доступен; иначе локальный durable-лог;
- Docker опрашивается через CLI и отсутствие Docker не считается ошибкой
  Guardian;
- владельцу Guardian пишет напрямую в Telegram, минуя Control Plane и
  Hermes (§25.5), потому что именно их падение он и должен уметь сообщить.

Guardian никогда не принимает входящих команд и не исполняет действий
владельца (§25.5).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path("/var/lib/helm-guardian")
PUBLIC_STATUS = STATE_DIR / "public-status.json"
ALERT_LOG = STATE_DIR / "alerts.jsonl"
METRIC_LOG = STATE_DIR / "metrics.jsonl"
SECRETS_DIR = Path("/etc/helm/secrets")

#: §25.3. Пороги применяются к устойчивому окну, а не к мгновенному пику:
#: сборка образа на минуту поднимает RAM, и будить владельца из-за этого
#: означает выучить его игнорировать алерты.
DISK_WARN, DISK_CRITICAL, DISK_EMERGENCY = 70.0, 82.0, 90.0
RAM_WARN, RAM_CRITICAL = 75.0, 88.0
SUSTAINED_WINDOW_SAMPLES = 3  # 3 замера по 5 минут = 15 минут устойчивого давления

OK, WARN, CRITICAL = "ok", "warn", "critical"
_SEVERITY_ORDER = {OK: 0, WARN: 1, CRITICAL: 2}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Check:
    name: str
    status: str = OK
    value: float | None = None
    unit: str | None = None
    threshold: float | None = None
    detail: str | None = None

    def worse_than(self, other: str) -> bool:
        return _SEVERITY_ORDER[self.status] > _SEVERITY_ORDER[other]


@dataclass
class Report:
    generated_at: str = field(default_factory=now_iso)
    checks: list[Check] = field(default_factory=list)

    @property
    def overall(self) -> str:
        worst = OK
        for check in self.checks:
            if check.worse_than(worst):
                worst = check.status
        return worst


# ── ресурсы хоста ───────────────────────────────────────────────────────────

def check_disk(path: str = "/") -> Check:
    usage = shutil.disk_usage(path)
    percent = usage.used / usage.total * 100
    status = OK
    if percent >= DISK_EMERGENCY:
        status = CRITICAL
    elif percent >= DISK_CRITICAL:
        status = CRITICAL
    elif percent >= DISK_WARN:
        status = WARN
    # Бриф панели §2 запрещает процент без абсолюта — отдаём и то и другое.
    return Check("disk", status, round(percent, 1), "%", DISK_WARN,
                 f"{usage.used // 2**30} / {usage.total // 2**30} GB")


def check_inodes(path: str = "/") -> Check:
    try:
        st = os.statvfs(path)
    except OSError as exc:
        return Check("inodes", WARN, detail=f"недоступно: {exc}")
    if st.f_files == 0:
        return Check("inodes", OK, detail="файловая система не сообщает inodes")
    used = (st.f_files - st.f_ffree) / st.f_files * 100
    status = CRITICAL if used >= DISK_CRITICAL else WARN if used >= DISK_WARN else OK
    return Check("inodes", status, round(used, 1), "%", DISK_WARN,
                 f"{st.f_files - st.f_ffree} / {st.f_files}")


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            values[key] = int(parts[0])  # в килобайтах
    return values


def check_ram() -> Check:
    mem = _meminfo()
    total, available = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    if not total:
        return Check("ram", WARN, detail="/proc/meminfo не читается")
    used_percent = (total - available) / total * 100
    status = CRITICAL if used_percent >= RAM_CRITICAL else WARN if used_percent >= RAM_WARN else OK
    return Check("ram", status, round(used_percent, 1), "%", RAM_WARN,
                 f"{(total - available) // 1024} / {total // 1024} MB")


def check_swap() -> Check:
    mem = _meminfo()
    total, free = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)
    if not total:
        return Check("swap", OK, 0.0, "%", detail="swap не настроен")
    used = (total - free) / total * 100
    return Check("swap", WARN if used >= 50 else OK, round(used, 1), "%", 50.0,
                 f"{(total - free) // 1024} / {total // 1024} MB")


def check_load() -> Check:
    one, five, fifteen = os.getloadavg()
    cpus = os.cpu_count() or 1
    ratio = five / cpus
    return Check("load", WARN if ratio >= 2.0 else OK, round(five, 2), "load5", 2.0 * cpus,
                 f"{one:.2f} / {five:.2f} / {fifteen:.2f} на {cpus} vCPU")


# ── сервисы ─────────────────────────────────────────────────────────────────

def _tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_port(name: str, host: str, port: int, critical: bool = True) -> Check:
    if _tcp_open(host, port):
        return Check(name, OK, detail=f"{host}:{port} отвечает")
    return Check(name, CRITICAL if critical else WARN, detail=f"{host}:{port} не отвечает")


def check_http(name: str, url: str, timeout: float = 3.0) -> Check:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            code = response.status
        return Check(name, OK if code < 400 else WARN, float(code), "http", detail=url)
    except Exception as exc:
        return Check(name, CRITICAL, detail=f"{type(exc).__name__}: {exc}")


def check_docker() -> list[Check]:
    """Docker опрашивается через CLI.

    Отсутствие Docker — не сбой Guardian: §30.10 требует, чтобы при
    остановленном Docker Guardian остался жив и записал алерт.
    """
    if shutil.which("docker") is None:
        return [Check("docker", WARN, detail="docker не установлен")]
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return [Check("docker", CRITICAL, detail=f"docker недоступен: {exc}")]
    if out.returncode != 0:
        return [Check("docker", CRITICAL, detail=(out.stderr or "").strip()[:200])]

    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    unhealthy = [ln.split("\t")[0] for ln in lines if "unhealthy" in ln.lower()]
    checks = [Check("docker", CRITICAL if unhealthy else OK, float(len(lines)), "containers",
                    detail=f"нездоровы: {', '.join(unhealthy)}" if unhealthy else "все здоровы")]
    return checks


def check_tls(host: str, port: int = 443, warn_days: int = 21) -> Check:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                not_after = tls.getpeercert()["notAfter"]
        expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days = (expires - datetime.now(timezone.utc)).days
        status = CRITICAL if days <= 7 else WARN if days <= warn_days else OK
        return Check(f"tls:{host}", status, float(days), "days", float(warn_days),
                     f"до {expires.date().isoformat()}")
    except Exception as exc:
        return Check(f"tls:{host}", WARN, detail=f"{type(exc).__name__}: {exc}")


def check_backup_age(marker: Path, warn_hours: int, critical_hours: int, name: str) -> Check:
    """Возраст последнего бэкапа / restore-теста (§25.2, §26.4)."""
    if not marker.exists():
        return Check(name, CRITICAL, detail=f"{marker} отсутствует — бэкап никогда не проходил")
    age_hours = (time.time() - marker.stat().st_mtime) / 3600
    status = CRITICAL if age_hours >= critical_hours else WARN if age_hours >= warn_hours else OK
    return Check(name, status, round(age_hours, 1), "hours", float(warn_hours))


# ── устойчивость порогов ────────────────────────────────────────────────────

def load_recent(metric: str, samples: int) -> list[float]:
    """Последние значения метрики из локального лога.

    Локальный лог, а не Postgres: устойчивость порога нужно уметь считать в
    том числе тогда, когда Postgres и есть упавший сервис.
    """
    if not METRIC_LOG.exists():
        return []
    values: list[float] = []
    with METRIC_LOG.open(encoding="utf-8") as handle:
        for line in handle.readlines()[-samples * 40:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("metric") == metric and row.get("value") is not None:
                values.append(float(row["value"]))
    return values[-samples:]


def apply_sustained(check: Check) -> Check:
    """Понизить мгновенный пик до OK, если давление не устойчиво (§25.3)."""
    if check.name not in ("ram", "load") or check.status == OK or check.value is None:
        return check
    history = load_recent(check.name, SUSTAINED_WINDOW_SAMPLES)
    if len(history) < SUSTAINED_WINDOW_SAMPLES:
        return check  # истории мало — не глушим, лучше лишний раз предупредить
    threshold = RAM_WARN if check.name == "ram" else (check.threshold or 0)
    if all(value >= threshold for value in history):
        return check
    check.status = OK
    check.detail = f"{check.detail or ''} · пик не устойчив, {SUSTAINED_WINDOW_SAMPLES} замеров".strip(" ·")
    return check


# ── вывод ───────────────────────────────────────────────────────────────────

def write_public_status(report: Report) -> None:
    """Санитизированный статус для /guardian/status.json (§10.5.9, §25.5).

    Наружу уходит ровно три поля. Ни имён контейнеров, ни объёмов диска, ни
    названий сервисов: §30.7 требует, чтобы публичный статус не раскрывал
    приватные метрики, а он доступен без аутентификации.
    """
    payload = {
        "status": report.overall,
        "generated_at": report.generated_at,
        "degraded": report.overall != OK,
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Атомарная запись: Caddy может читать файл в момент обновления, и
    # половина JSON хуже, чем прошлая версия целиком.
    tmp = PUBLIC_STATUS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PUBLIC_STATUS)


def append_jsonl(path: Path, row: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def send_direct_alert(text: str) -> bool:
    """Прямое сообщение владельцу в обход Control Plane и Hermes (§25.5)."""
    try:
        token = (SECRETS_DIR / "guardian_telegram_token").read_text().strip()
        chat_id = (SECRETS_DIR / "telegram_owner_id").read_text().strip()
    except OSError:
        return False

    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status == 200
    except Exception:
        return False


def run_once(targets: dict | None = None) -> Report:
    targets = targets or {}
    report = Report()
    report.checks.extend([
        apply_sustained(check_disk()),
        check_inodes(),
        apply_sustained(check_ram()),
        check_swap(),
        apply_sustained(check_load()),
    ])
    report.checks.extend(check_docker())
    report.checks.append(check_port("postgres", "127.0.0.1", targets.get("postgres_port", 5432)))
    report.checks.append(check_http("control-plane",
                                    targets.get("control_plane", "http://127.0.0.1:8080/healthz")))
    report.checks.append(check_port("litellm", "127.0.0.1", targets.get("litellm_port", 4000)))
    report.checks.append(check_port("hermes", "127.0.0.1", targets.get("hermes_port", 8765), False))
    report.checks.append(check_backup_age(
        Path("/var/lib/helm-guardian/last-backup"), 26, 50, "backup_age"))
    report.checks.append(check_backup_age(
        Path("/var/lib/helm-guardian/last-restore-test"), 24 * 8, 24 * 14, "restore_test_age"))
    for host in targets.get("tls_hosts", []):
        report.checks.append(check_tls(host))

    for check in report.checks:
        if check.value is not None:
            append_jsonl(METRIC_LOG, {"at": report.generated_at, "metric": check.name,
                                      "value": check.value, "unit": check.unit})

    write_public_status(report)

    if report.overall == CRITICAL:
        failing = [c for c in report.checks if c.status == CRITICAL]
        text = "HELM Guardian: CRITICAL\n" + "\n".join(
            f"· {c.name}: {c.detail or c.value}" for c in failing
        )
        delivered = send_direct_alert(text)
        # Недоставленный алерт остаётся в durable-логе и будет повторён
        # (§25.5): молча потерять его — худший исход из возможных.
        append_jsonl(ALERT_LOG, {"at": report.generated_at, "severity": CRITICAL,
                                 "delivered": delivered,
                                 "checks": [asdict(c) for c in failing]})
    return report


def main() -> int:
    report = run_once()
    print(json.dumps({"overall": report.overall,
                      "checks": [asdict(c) for c in report.checks]},
                     ensure_ascii=False, indent=2))
    return 0 if report.overall != CRITICAL else 2


if __name__ == "__main__":
    sys.exit(main())
