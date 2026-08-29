"""§4.6 и §30.9: публичный периметр.

Caddyfile — граница между «внутри» и «в интернете». Ошибка здесь не падает
и не логируется: она просто публикует то, что публиковать нельзя, и это
обнаруживается сканером раньше, чем владельцем. Поэтому граница проверяется
тестом.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = ROOT / "config" / "Caddyfile"
COMPOSE = ROOT / "compose" / "docker-compose.yml"

#: §4.6 «Не публиковать»: PostgreSQL, LiteLLM, Hermes API/dashboard,
#: admin API Control Plane, редактор n8n, внутренняя БД SignalAI.
#: 8090 — локальный chief API Hermes (плагин max-bridge, §10.2 «127.0.0.1
#: only»): по нему сообщение попадает прямо к модели минуя всё остальное.
NEVER_PUBLIC_PORTS = {5432, 4000, 5678, 3306, 6379, 8090}

#: Единственные публичные пути (§4.6).
ALLOWED_PUBLIC_PREFIXES = (
    "/",                       # статика панели
    "/api/panel/v1/",
    "/auth/",
    "/hooks/",
    "/guardian/status.json",
)


def caddy_directives() -> list[str]:
    """Строки Caddyfile без комментариев."""
    return [
        line.split("#", 1)[0].strip()
        for line in CADDYFILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_only_caddy_publishes_ports():
    """Единственный сервис с публичными портами — Caddy (§4.6)."""
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        for mapping in service.get("ports", []):
            parts = str(mapping).split(":")
            if len(parts) == 3:          # host_ip:host_port:container_port
                host_ip, host_port = parts[0], int(parts[1])
            else:                         # host_port:container_port
                host_ip, host_port = "0.0.0.0", int(parts[0])

            if name == "caddy":
                assert host_port in (80, 443), f"caddy публикует лишний порт {host_port}"
                continue

            assert host_ip in ("127.0.0.1", "::1"), \
                f"сервис {name} публикует {host_port} на {host_ip} — §4.6 это запрещает"
            assert host_port not in NEVER_PUBLIC_PORTS or host_ip == "127.0.0.1", \
                f"{name}: порт {host_port} не должен быть доступен извне"


def test_no_service_binds_wildcard_except_caddy():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        if name == "caddy":
            continue
        for mapping in service.get("ports", []):
            assert str(mapping).startswith("127.0.0.1:"), \
                f"{name}: {mapping} слушает не только loopback"


def site_blocks() -> dict[str, list[str]]:
    """Разбор Caddyfile по блокам сайтов.

    Нужен именно поблочный разбор: git.cmpas.ru проксируется целиком и это
    верно — §4.6 перечисляет его как публичный. А в helm.cmpas.ru живут и
    панель, и API, и там catch-all недопустим. Без разделения проверка
    приписала бы директиву Forgejo блоку панели.
    """
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    depth = 0
    for line in caddy_directives():
        if current is None:
            if line.endswith("{") and not line.startswith(("(", "{")):
                current = line[:-1].strip()
                blocks[current] = []
                depth = 1
            continue
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            current = None
            continue
        blocks[current].append(line)
    return blocks


def test_helm_domain_has_no_catch_all_proxy():
    """В helm.cmpas.ru проксирование только по точным путям.

    Catch-all здесь открыл бы internal API: `handle { reverse_proxy }`
    отдаёт наружу всё, включая /internal/*.
    """
    lines = site_blocks()["helm.cmpas.ru"]
    depth_of_catch_all = None
    depth = 0
    for line in lines:
        if line.startswith("handle") and line.rstrip().endswith("{"):
            target = line[len("handle"):-1].strip()
            if target == "":
                depth_of_catch_all = depth
        if depth_of_catch_all is not None and line.startswith("reverse_proxy"):
            pytest.fail(f"reverse_proxy внутри catch-all handle: {line}")
        depth += line.count("{") - line.count("}")
        if depth_of_catch_all is not None and depth <= depth_of_catch_all:
            depth_of_catch_all = None


def test_forgejo_domain_is_separate():
    """Forgejo проксируется целиком, но на своём домене (§4.6)."""
    blocks = site_blocks()
    assert "git.cmpas.ru" in blocks
    assert any(l.startswith("reverse_proxy") for l in blocks["git.cmpas.ru"])
    # И он не должен появляться внутри домена панели.
    assert not any("3000" in l for l in blocks["helm.cmpas.ru"])


def test_unknown_host_reveals_nothing():
    """Обращение по IP не должно раскрывать, что здесь что-то есть."""
    blocks = site_blocks()
    fallback = blocks.get(":443")
    assert fallback is not None, "нет блока для обращения по IP/неизвестному имени"
    assert any("respond 404" in l for l in fallback)


def test_internal_api_is_not_routed():
    """/internal/* не должен встречаться в публичной конфигурации вовсе."""
    body = "\n".join(caddy_directives())
    assert "/internal" not in body, "internal API попал в публичный периметр"


def test_never_public_ports_are_not_proxied():
    """Ни один из закрытых портов не должен стоять целью reverse_proxy.

    В первую очередь про 8090: локальный chief API Hermes (§10.2) отдаёт
    сообщение прямо модели, минуя регистрацию задачи и дедуп, — публичный
    маршрут на него обошёл бы весь Control Plane целиком.
    """
    for line in caddy_directives():
        if not line.startswith("reverse_proxy"):
            continue
        for port in NEVER_PUBLIC_PORTS:
            assert f":{port}" not in line, f"закрытый порт {port} проксируется: {line}"


def test_only_named_webhooks_are_public():
    """§4.6: «только явно зарегистрированные внешние webhooks»."""
    body = "\n".join(caddy_directives())
    hook_routes = re.findall(r"handle\s+(/hooks/\S*)", body)
    for route in hook_routes:
        assert "*" not in route, f"вебхук {route} объявлен через wildcard"


def test_guardian_status_is_served_from_file():
    """Статус Guardian обязан работать при мёртвом Control Plane (§25.5)."""
    body = "\n".join(caddy_directives())
    # handle_path, не handle: префикс срезается из URI (см. комментарий в
    # Caddyfile — найдено на реальном P2 bring-up, тест обновлён вслед)
    block = body[body.index("handle_path /guardian/*"):]
    block = block[: block.index("handle", 1)] if "handle" in block[1:] else block
    assert "file_server" in block, "статус Guardian проксируется, а не отдаётся файлом"
    assert "reverse_proxy" not in block


def test_security_headers_configured():
    body = "\n".join(caddy_directives())
    for header in ("Strict-Transport-Security", "X-Content-Type-Options",
                   "X-Frame-Options", "Referrer-Policy", "Content-Security-Policy"):
        assert header in body, f"нет заголовка {header}"
    assert "frame-ancestors 'none'" in body


def test_images_are_pinned():
    """§33 «pin versions/images»: плавающий :latest меняет мажор ночью."""
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        image = service.get("image")
        if image is None or "build" in service:
            continue
        assert ":" in image, f"{name}: образ без тега"
        tag = image.rsplit(":", 1)[1]
        assert tag != "latest", f"{name}: образ запинован на latest"


def test_logs_are_bounded():
    """§25.6: без ограничения логи съедают диск быстрее, чем растут данные."""
    raw = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    logging = raw.get("x-logging", {})
    assert logging.get("options", {}).get("max-size"), "не задан max-size логов"
    assert logging.get("options", {}).get("max-file"), "не задан max-file логов"
