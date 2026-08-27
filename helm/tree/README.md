# `helm/tree` → `/opt/helm`

Дерево отображается на `/opt/helm` целевого VPS один в один (ТЗ §5).
Развёртывание — копирование и запуск, а не применение патчей. Почему сборка
идёт офлайн, а не по SSH — `docs/adr/ADR-017-offline-build.md`.

```
compose/          docker-compose + init-скрипты БД
control-plane/    helm-core: FastAPI, 16 таблиц, policy, реестр, approvals
guardian/         независимый watchdog + systemd-юниты + автоочистка
config/           actions.yaml, Caddyfile, конфиги моделей
panel/            production-фронтенд + проверенные дизайн-исходники
scripts/          checkpoint.sh — фазовый откат без Git (§31.0)
docs/adr/         решения, фактически принятые при сборке
```

## Что уже проверено

| Компонент | Проверка | Результат |
|---|---|---|
| Control Plane | 49 тестов против настоящего PostgreSQL 16 | проходят |
| §30.2 | девять обязательных тестов, по одному на строку ТЗ | проходят |
| A-DoD п.5–6 | RED без одобрения блокируется; после одобрения — ровно один раз | доказано |
| Миграции | применение, сверка с моделями, откат, повторное применение | проходят |
| Guardian | §30.10 — жив при мёртвых Docker/PG/CP/Hermes | проходит |
| Периметр §4.6 | наружу только Caddy; internal API не маршрутизируется | проходит |
| Панель | сборка, 27 проверок брифа, 3 вьюпорта в Chromium | проходят |

Наборы проверены мутациями: снятие RED-гейта, сверки хэша, различения
повтора, catch-all в Caddy и публикация Postgres наружу — каждое ловится
своим тестом.

## Чего здесь нет и почему

Не собрано офлайн то, что требует живого сервера или отсутствующих секретов:
Hermes и Telegram (P4), LiteLLM с реальным вызовом OpenRouter (P3), restic и
restore-тест (P5), n8n (P6), Forgejo (P6.5), MAX и MCP (P7), auth панели
(P7.5 — нужен BotFather), домены (P9–P12).

Реализация auth панели присутствует со стороны хранения и step-up
(`panel_stepup_challenges`, `deps.require_stepup`), но эндпоинты
`/auth/telegram/*` и `/auth/passkey/*` требуют значений от BotFather и
пишутся в P7.5.

## Запуск тестов

```bash
# Control Plane (нужен PostgreSQL)
cd control-plane
HELM_TEST_DB='postgresql+psycopg://helm@/helm_test?host=/tmp&port=55432' \
HELM_POLICY=../config/policies/actions.yaml pytest tests/ -q

# Guardian
cd guardian && pytest tests/ -q

# Панель
cd panel && npm ci && npm run build
node tests/brief-compliance.mjs && node tests/screenshot.mjs
```
