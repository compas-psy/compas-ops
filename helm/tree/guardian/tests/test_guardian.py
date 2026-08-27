"""§30.10: Guardian остаётся жив при остановленных Docker/Postgres/CP/Hermes/n8n."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import guardian


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(guardian, "STATE_DIR", tmp_path)
    monkeypatch.setattr(guardian, "PUBLIC_STATUS", tmp_path / "public-status.json")
    monkeypatch.setattr(guardian, "ALERT_LOG", tmp_path / "alerts.jsonl")
    monkeypatch.setattr(guardian, "METRIC_LOG", tmp_path / "metrics.jsonl")
    monkeypatch.setattr(guardian, "SECRETS_DIR", tmp_path / "secrets")
    return tmp_path


def test_survives_everything_down(isolated_state, monkeypatch):
    """Главный тест §30.10. Всё лежит — Guardian жив и записал алерт."""
    monkeypatch.setattr(guardian.shutil, "which", lambda name: None)      # docker нет
    monkeypatch.setattr(guardian, "_tcp_open", lambda *a, **k: False)     # PG/LiteLLM/Hermes нет
    monkeypatch.setattr(guardian, "check_http",
                        lambda name, url, timeout=3.0: guardian.Check(name, guardian.CRITICAL,
                                                                      detail="недоступен"))

    report = guardian.run_once()

    assert report.overall == guardian.CRITICAL
    assert (isolated_state / "alerts.jsonl").exists(), "алерт обязан быть записан локально"
    alert = json.loads((isolated_state / "alerts.jsonl").read_text().splitlines()[0])
    assert alert["delivered"] is False, "канал недоступен — но факт зафиксирован, не потерян"
    names = {c["name"] for c in alert["checks"]}
    assert {"postgres", "control-plane"} <= names


def test_public_status_leaks_nothing(isolated_state, monkeypatch):
    """§30.7: публичный статус не раскрывает приватных метрик."""
    monkeypatch.setattr(guardian.shutil, "which", lambda name: None)
    monkeypatch.setattr(guardian, "_tcp_open", lambda *a, **k: False)
    monkeypatch.setattr(guardian, "check_http",
                        lambda name, url, timeout=3.0: guardian.Check(name, guardian.OK))
    guardian.run_once()

    payload = json.loads((isolated_state / "public-status.json").read_text())
    assert set(payload) == {"status", "generated_at", "degraded"}, \
        "наружу уходят ровно три поля, иначе публичный эндпоинт раскрывает состояние"
    raw = json.dumps(payload)
    for leak in ("GB", "MB", "postgres", "docker", "hermes", "litellm", "/"):
        assert leak not in raw or leak == "/", f"утечка {leak!r} в публичный статус"


def test_public_status_written_atomically(isolated_state, monkeypatch):
    """Caddy читает файл в момент обновления: половина JSON недопустима."""
    replaced = []
    real_replace = guardian.os.replace
    monkeypatch.setattr(guardian.os, "replace",
                        lambda src, dst: (replaced.append((src, dst)), real_replace(src, dst))[1])
    guardian.write_public_status(guardian.Report())
    assert replaced, "запись обязана идти через os.replace, а не прямым open(...).write"
    assert json.loads((isolated_state / "public-status.json").read_text())["status"] == "ok"


def test_uses_only_stdlib():
    """Зависимость, которую нельзя импортировать, — Guardian, который не запустился."""
    import ast

    source = Path(guardian.__file__).read_text(encoding="utf-8")
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])

    third_party = roots - set(sys.stdlib_module_names) - {"guardian"}
    assert not third_party, f"Guardian тянет сторонние пакеты: {sorted(third_party)}"


def test_sustained_window_suppresses_a_spike(isolated_state):
    """§25.3: мгновенный пик RAM не будит владельца."""
    for value in (10.0, 12.0, 11.0):
        guardian.append_jsonl(guardian.METRIC_LOG,
                              {"at": "x", "metric": "ram", "value": value, "unit": "%"})
    spike = guardian.Check("ram", guardian.WARN, 80.0, "%", guardian.RAM_WARN)
    assert guardian.apply_sustained(spike).status == guardian.OK


def test_sustained_window_keeps_real_pressure(isolated_state):
    """Устойчивое давление не глушится."""
    for value in (79.0, 81.0, 84.0):
        guardian.append_jsonl(guardian.METRIC_LOG,
                              {"at": "x", "metric": "ram", "value": value, "unit": "%"})
    real = guardian.Check("ram", guardian.WARN, 85.0, "%", guardian.RAM_WARN)
    assert guardian.apply_sustained(real).status == guardian.WARN


def test_missing_backup_marker_is_critical(isolated_state):
    """Бэкап, которого никогда не было, — не «ok, данных нет»."""
    check = guardian.check_backup_age(isolated_state / "never", 26, 50, "backup_age")
    assert check.status == guardian.CRITICAL


# ── §25.6: закрытый список разрешённой автоочистки ──────────────────────────

CLEANUP = Path(__file__).resolve().parents[1] / "cleanup.sh"

#: Вызовы, которых не должно быть ни при каком заполнении диска (§25.6).
#: Диск на 90% — инцидент; удалённый named volume — потеря данных.
FORBIDDEN_CLEANUP = (
    "system prune",
    "volume rm",
    "volume prune",
    "--volumes",
    "drop database",
    "dropdb",
)


def _executable_lines(path: Path) -> list[str]:
    """Строки скрипта без комментариев: запрет в комментарии — не вызов."""
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped.split("#", 1)[0].strip().lower())
    return lines


def test_cleanup_never_calls_forbidden_commands():
    body = "\n".join(_executable_lines(CLEANUP))
    for forbidden in FORBIDDEN_CLEANUP:
        assert forbidden not in body, \
            f"cleanup.sh содержит запрещённый §25.6 вызов {forbidden!r}"


def test_cleanup_check_would_catch_a_real_call(tmp_path):
    """Проверка самой проверки: настоящий вызов обязан быть найден."""
    probe = tmp_path / "probe.sh"
    probe.write_text("# docker system prune запрещён\ndocker system prune -a --volumes\n",
                     encoding="utf-8")
    body = "\n".join(_executable_lines(probe))
    assert any(f in body for f in FORBIDDEN_CLEANUP)


def test_cleanup_defaults_to_dry_run():
    """Без --apply скрипт обязан только показывать, а не удалять."""
    import subprocess

    out = subprocess.run(["bash", str(CLEANUP)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0
    assert "DRY-RUN" in out.stdout or "docker не установлен" in out.stdout
