# WORKPLAN — HELM v3.3

Живой файл состояния реализации (ТЗ §31.0). Обновляется оркестратором после
каждой значимой задачи — это заменяет необходимость владельцу писать
«продолжай».

## Текущая фаза: **P4 закрыт.** P5 (Guardian) развёрнут и работает live. Backup — в процессе (restic/rclone поверх Яндекс Диска, WebDAV 401 — диагностируется).

## Распоряжение владельца: backup — двухэтапный, P5 не покрывает всё (зафиксировано 29.08.2026)

**P5 backup покрывает только:** PostgreSQL (все БД кластера), `/opt/helm/config`, `/opt/helm/guardian`, `/etc/helm/secrets`, состояние/профили/kanban Hermes. **Не считать backup-подсистему завершённой для системы в целом** — это первый этап, не финал.

**В P6.5, после установки Forgejo:**
1. Импортировать репозитории: `cmpas.ru`, `zapiski`, `compas-voice`, `compas-ops`, `signalAI-mobileApp`, `helm-infra`.
2. Включить в тот же encrypted restic offsite-бэкап (не отдельный механизм): Forgejo repositories, Forgejo DB/config, используемые PR metadata/attachments.
3. GitHub mirror остаётся ДОПОЛНИТЕЛЬНОЙ внешней копией — не заменяет restic offsite backup.
4. После расширения backup — restore-test минимум одного Forgejo repository с проверкой `refs`/`tags`/`HEAD`, отдельно от Postgres-restore-теста P5.

## Пройдено офлайн (до переноса на сервер, session 0afed5d1)

| Фаза | Что сделано | Evidence |
|---|---|---|
| P0 (частично) | SHA256 трёх дизайн-исходников сверены и совпали; ограничения среды зафиксированы (ADR-017) | `panel/design-source/SOURCE_HASHES.txt` |
| P2 (офлайн-часть) | 16 таблиц, policy-движок, реестр действий, approvals, дедупликация, миграции Alembic | 49 тестов проходят на настоящем PostgreSQL 16 |
| P5 (офлайн-часть) | Guardian: независимость от Docker/PG/CP/Hermes, автоочистка §25.6 | 10 тестов, мутационная проверка |
| P7.5 (офлайн-часть) | Production-фронтенд панели: 5 разделов, step-up, карточка одобрения | сборка + 27 проверок брифа + 3 вьюпорта в Chromium |
| P1 (инфраструктура) | compose, Caddyfile-периметр §4.6, checkpoint.sh | 11 тестов периметра |

## На сервере (185.250.44.137), этот цикл

- [x] Дерево `/opt/helm` развёрнуто с сервера (scp), права нормализованы (755/644)
- [x] Секреты B2–B4 (`openrouter_api_key`, `telegram_bot_token`, `telegram_owner_id`, `backup_credentials`) в `/root/helm-bootstrap`, владелец разместил сам — значения агенту не передавались
- [ ] Спека `HELM_FINAL_v3.3_2026-08-27.md` в `/root/helm-bootstrap` — не подтверждена; не блокирует
- [ ] OS update
- [ ] Timezone → Europe/Moscow
- [x] Каталоги §5 созданы
- [x] `/opt/helm-state/*`, `/etc/helm/{secrets,ssh,backup}` (0700)
- [x] admin-пользователь `helm` + ключ + **второй независимый вход подтверждён** (`helm` → `sudo` → `root`)
- [x] firewall (22/80/443) — ufw active, default deny incoming, explicit allow на 22/80/443 (v4+v6); подтверждено 28.08 перед подъёмом Caddy
- [x] Docker + docker-compose plugin, daemon.json (bounded logs), hello-world подтверждён
- [x] Caddy + TLS — поднят на живом сервере, см. раздел "P2 — Control Plane" ниже
- [x] B7 подтверждён владельцем — доступ к консоли/rescue хостера есть
- [x] password-login и root SSH отключены (`10-helm-hardening.conf`, reload проверен свежим подключением)
- [x] firewall (22/80/443) — см. выше
- [ ] bounded journald logs
- [x] Docker + docker-compose plugin, daemon.json (bounded logs), hello-world подтверждён
- [x] Caddy + TLS — см. выше

## P2 — Control Plane на живом сервере

- [x] `alembic upgrade head` применён к реальной БД: 17 таблиц (16 + `alembic_version`)
- [x] `post-migration.sql` применён: append-only lockdown на `task_events`, права `helm_app`
- [x] `helm-core` поднят и подтверждён живым: `GET /healthz` с хоста → `200` (не только внутренний
      Docker healthcheck — тот проходил и до фикса, см. находку ниже)
- [x] Caddy + TLS — поднят, реальные сертификаты Let's Encrypt для `helm.cmpas.ru` и `git.cmpas.ru`
      получены. `https://helm.cmpas.ru/` → `200` (панель), `/guardian/status.json` → санитизированный
      плейсхолдер (Guardian сам ещё не установлен, это P5), `https://git.cmpas.ru/` → честные `503`
      (Forgejo — P6.5, ещё не установлен, Caddyfile не проксирует в пустоту — работает как задумано)

**Найдено и исправлено на этом bring-up:**
1. `uvicorn --host 127.0.0.1` слушал loopback самого контейнера, а не хоста — Docker healthcheck
   (исполняется внутри namespace контейнера) показывал `healthy`, но `curl` с хоста получал
   connection refused. Исправлено: `--host 0.0.0.0` в `control-plane/Dockerfile`.
2. Тот же класс ошибки в `Caddyfile`: `reverse_proxy 127.0.0.1:PORT` предполагает loopback хоста,
   а в bridge-сети (как было в compose) это loopback самого контейнера Caddy. Исправлено:
   `network_mode: host` у `caddy` в `docker-compose.yml`.
3. `Caddyfile`: `handle /guardian/status.json` не срезает совпавший префикс из URI (в отличие от
   `handle_path`) — `file_server` искал `/srv/guardian/guardian/status.json` вместо примонтированного
   `/srv/guardian/status.json`. Исправлено: `handle_path /guardian/*`.
4. `panel/dist` в `.gitignore` (верно — это billed-артефакт), поэтому обычный `git pull` на машине
   владельца никогда его не привозил; `/opt/helm/panel/dist` на сервере оказался пустым. Собранная
   офлайн панель (P7.5, уже прошла 27 проверок брифа) передана отдельным файлом и разложена вручную.
5. `caddy reload` не работает, пока в `Caddyfile` стоит `admin off` (сознательное решение — не
   открывать admin API): правки `Caddyfile` требуют `docker compose restart caddy`, не `reload`.

**Известный некритичный хвост:** плейсхолдер `/var/lib/helm-guardian/public-status.json` создан
через PowerShell `Set-Content -Encoding utf8`, которая добавляет BOM — ответ `/guardian/status.json`
начинается с невидимого BOM-байта. `fetch().json()` в браузере штатно съедает BOM при UTF-8-декодировании,
так что панель не должна на этом споткнуться; файл в любом случае временный — Guardian в P5 перезапишет
его питоновским `json.dumps` (без BOM). Не исправлялось отдельно ради файла, который скоро исчезнет сам.

## P3 — LiteLLM на живом сервере

**Находка, определившая весь ход фазы:** OpenRouter блокирует этот VPS по
IP/датацентру — `curl` к `openrouter.ai` (даже без авторизации) получал
честный TLS-сертификат `openrouter.ai` (не MITM), но `HTTP/2 403` от
`server: cloudflare` с телом `{"success": false, "error": "Access denied by
security policy."}`. С обычной машины владельца тот же запрос — `200` с
реальным каталогом. Не Россия целиком — конкретно диапазон этого хостера.
`cf-ray` для возможного тикета в поддержку OpenRouter: `a322f17feeeaf102-DME`,
28.08.2026 11:23:20 GMT.

**Решение владельца:** обход только для трафика к OpenRouter (не общий VPN
сервера) через собственный VPS в Финляндии владельца, протокол `mieru`.

- [x] Секрет: пароль `mieru` пришёл файлом в чат — по правилу этого же
      `CLAUDE.md §5.4` это означает «токен скомпрометирован, `/human`
      задача»; ротация — решение владельца, агент её не форсирует. Значение
      нигде не закоммичено — только `scp` напрямую на сервер.
- [x] Клиент — `enfein/mbox` (`sing-box 1.13.19` + `mieru 3.36.0`, форк от
      автора самого mieru; не Karing — тот GUI/Flutter, для headless
      сервера не подходит). `.deb` с GitHub releases, ставит юзера
      `sing-box` (uid 987, `CAP_DAC_READ_SEARCH`) и шаблонный юнит
      `sing-box@.service`.
- [x] Конфиг `/etc/sing-box/openrouter-proxy.json` (0600 root:root):
      локальный `mixed`-inbound `127.0.0.1:18080`, маршрутизация — только
      `openrouter.ai`/`*.openrouter.ai` через `mieru-out`, всё остальное
      (apt/git/Telegram/Let's Encrypt) — `final: direct`. Не общий конфиг
      владельца (тот заворачивал бы весь трафик и тянул неприменимые
      корп-DNS/LAN правила) — написан заново под сервер.
- [x] Сервис: `sing-box@openrouter-proxy.service`, `enable --now`.
      `curl -x http://127.0.0.1:18080 https://openrouter.ai/api/v1/models`
      → `200`.
- [x] `docker-compose.yml`: `litellm` получил `network_mode: host` (та же
      причина, что у `caddy` — `HTTPS_PROXY=127.0.0.1:18080` это loopback
      ХОСТА) + `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY`. Из-за смены сети
      `litellm_database_url` пришлось поправить `@postgres:5432` →
      `@127.0.0.1:5432` (Docker DNS `postgres` недоступен вне bridge-сети).
- [x] Матрица кандидатов §15.6 сверена целиком с живым каталогом через
      прокси — все ID совпали, кроме `moonshotai/kimi-k2.7-code` (в спеке):
      актуальная версия в каталоге на 28.08.2026 — `kimi-k3` (снимок
      `kimi-k3-20260715`). Есть и floating `kimi-latest`, но проект
      пинует версии моделей (как и Docker-образы, `docker-compose.yml`
      шапка) — не используется.
- [x] `config/models/litellm.yaml` написан: provisional primary+fallback
      для `helm-router`/`helm-cheap`/`helm-standard` (единственная тройка,
      обязательная для Milestone A по §15.3 п.9); `helm-code`/`helm-review`/
      `helm-board`/`helm-longhorizon` зарегистрированы одной моделью каждый
      без выбора primary/fallback — это не требуется сейчас, остальные
      кандидаты матрицы — в комментариях файла, не в конфиге (не создавать
      случайную балансировку между непроверенными моделями).
- [ ] **Не проверено эмпирически:** поддерживает ли образ litellm
      (`ghcr.io/berriai/litellm:main-v1.55.8`) конвенцию `_FILE` для
      `OPENROUTER_API_KEY_FILE`/`DATABASE_URL_FILE`/`LITELLM_MASTER_KEY_FILE`
      так же, как официальный образ Postgres. Не факт по умолчанию (см.
      `helm_core/config.py`: HELM пришлось реализовывать это вручную,
      pydantic-settings сам не умеет) — первый запуск контейнера это
      покажет.
- [x] Подъём контейнера `litellm`, `/health/liveliness` → `200`
- [x] §15.3 п.10-12 — **P3-гейт §30.4 пройден полностью:**
      - реальный completion через `helm-standard` (`z-ai/glm-5` → OpenRouter →
        GMICloud): `{"content":"pong", ...}`
      - usage/cost logging доказан: ответ несёт `usage`/`cost`, в БД
        litellm созданы таблицы spend-учёта (`MonthlyGlobalSpend` и т.д.)
      - искусственный слом primary доказан: `helm-standard` временно указан
        на несуществующую модель (`sed` прямо на сервере, только внутри
        блока `helm-standard`, не задевая тот же `z-ai/glm-5` у `helm-code`)
        → ответ пришёл через `helm-standard-fallback`
        (`deepseek-v4-pro-0813` → Fireworks), тоже реальный `pong`.
        Конфигурация возвращена, повторно подтверждена (`glm-5` → Venice).
- [ ] Virtual keys для профилей Hermes (§15.3 п.7) — намеренно отложено:
      создавать ключи для профилей, которых ещё физически не существует
      (Hermes — P4), преждевременно. Делать перед стартом P4, не сейчас.

**Найдено и исправлено при закрытии гейта:** секрет `litellm_database_url`
содержал пароль со случайными символами `/` (похоже на base64-алфавит) —
Python `urlparse` разбирал строку терпимо (ищет ПОСЛЕДНИЙ `@`), а более
строгий парсер Prisma (Rust) — нет: путал границу authority/path, отсюда
`P1013: invalid port number`. Не percent-encoding постфактум (риск разойтись
в деталях кодирования с Prisma) — пароль роли `litellm` перегенерирован
чисто в hex-алфавите (`openssl rand -hex 32`, без единого зарезервированного
в URL символа) прямо на сервере, значение никуда не выводилось.

## P4 — Hermes на живом сервере

- [x] Установлен как пользователь `helm` (не root, не Docker — своим
      способом: `hermes gateway install` умеет systemd/launchd сам) через
      официальный `install.sh` (`curl | bash` от реального
      `hermes-agent.nousresearch.com`, репозиторий `NousResearch/hermes-agent`
      — то, на что реально ссылается спека, не выдумано)
- [x] `Hermes Agent v0.20.6 (2026.8.27) · upstream eff97a8a`, `hermes doctor` чист
- [x] **Известный риск снят эмпирически:** issue
      [#26489](https://github.com/NousResearch/hermes-agent/issues/26489) —
      зависание на 60-90с при `provider: custom` из-за проб Ollama-нативных
      эндпоинтов на LiteLLM (там честный 404). На `v0.20.6` не воспроизвелось —
      реальный запрос прошёл за 5-6 секунд, без таймаута.
- [x] Провайдер настроен: `model.provider: custom`, `model.base_url:
      http://127.0.0.1:4000/v1`, `model.default: helm-standard`,
      `model.api_key` = `litellm_master_key` (временно мастер-ключ — для
      финальной настройки нужны per-profile virtual keys, §15.3 п.7/§15.4)
- [x] **Реальный сквозной запрос:** `hermes -z 'Reply with exactly one
      word: pong.'` → `pong` за ~6 секунд. Полная цепочка Milestone A
      (`Hermes → LiteLLM → OpenRouter → модель`) работает.

**Найдено и обойдено:**
1. Node.js из инсталлятора падал с `error while loading shared libraries:
   libatomic.so.1` — на этом минимальном Ubuntu 24.04 пакета `libatomic1`
   не было. Установлен через apt.
2. `hermes-agent.nousresearch.com` (и, видимо, значительная часть
   PyPI/npm-подобной инфраструктуры) тоже банит IP этого VPS — тот же класс
   блокировки, что у OpenRouter, только через Vercel, не Cloudflare
   (`x-vercel-mitigated: deny`). Вместо точечного добавления доменов в
   `sing-box` (второй заблокированный сервис подряд — предвестник новых)
   маршрутизация упрощена: `route.final` = `mieru-out` для ВСЕГО, что идёт
   через локальный прокси-порт `18080` — на остальной трафик сервера
   (apt/git/прямые соединения) это не влияет, они этот порт не используют.
3. `.bashrc` в неинтерактивной ssh-сессии не выполняется дальше guard'а
   `case $- in *i*) ;; *) return;; esac` — PATH из инсталлятора не
   применяется через `source ~/.bashrc` при удалённых командах. Бинарник:
   `/home/helm/.local/bin/hermes`.
4. `OPENAI_API_KEY`/`OPENAI_BASE_URL` в `.env` не подхватились для
   `model.provider: custom` (судя по документации конфига, это скорее для
   `auxiliary`-моделей) — сработало явное документированное поле
   `model.api_key` через `hermes config set`.

**Отклонение от §5 плана (сознательное):** Hermes живёт в `~/.hermes`
(домашняя директория пользователя `helm`), не в `/opt/helm/hermes/` —
это жёстко зашитая конвенция самого инструмента (CLI, install.sh,
документация — везде `~/.hermes`), переносить её боролись бы с
инструментом без реальной выгоды.

**Telegram gateway + `helm-control` — поднято, A-DoD п.1-2-3 подтверждены на живом пути:**

- [x] `hermes gateway install --system` → `hermes-gateway.service` (systemd,
      юзер `helm`), `TELEGRAM_BOT_TOKEN`/`TELEGRAM_ALLOWED_USERS` в `~/.hermes/.env`
- [x] Плагин `helm-control` — после переезда с нерабочей директории
      `~/.hermes/hooks/` (см. находку 6 ниже) живёт в
      `~/.hermes/plugins/helm-control/` (`plugin.yaml` + `__init__.py` с
      `register(ctx)`), включён через `hermes plugins enable helm-control`
      (`plugins.enabled` в конфиге — opt-in по умолчанию). События
      `pre_gateway_dispatch` (регистрация задачи в Control Plane ДО
      LLM-вызова, fail-closed при недоступном CP — `{"action":"skip"}`,
      сообщение дальше не идёт) и `pre_llm_call` (передаёт `HELM_TASK_ID`
      коротким контекстом).
- [x] **A-DoD п.1 подтверждён живым сообщением**: реальное сообщение
      владельца в Telegram («Тест 9») получило реальный ответ модели через
      полную цепочку Hermes → LiteLLM → OpenRouter.
- [x] **A-DoD п.2 подтверждён на живом Telegram-пути** (не только прямым
      curl): задача `eb2cf4f0-...` в `tasks`, `origin_channel='telegram'`,
      `origin_owner_id='182398258'`, `created_at` секунда в секунду
      совпадает с моментом отправки «Тест 9» в Telegram.
- [x] **A-DoD п.3 (fail-closed) подтверждён вживую**: `helm-core` остановлен
      на ~1 минуту, отправлено тестовое сообщение — `register_task` упал с
      `Connection refused`, модель НЕ ответила (в отличие от всех
      предыдущих тестов), новая задача в `tasks` не создалась. `helm-core`
      поднят обратно, здоров. Собственное уведомление плагина в чат
      («HELM Control Plane недоступен») в этом прогоне до пользователя не
      дошло (см. открытую находку ниже) — не блокирует само свойство
      fail-closed (сообщение не дошло до модели — это и есть п.3), но
      желательно починить для UX.

**Найдено и исправлено при подъёме Telegram/плагина:**

6a. **Плагин был подключён не к той системе хуков и молча не гейтил ничего.**
    `~/.hermes/hooks/<name>/` (`HOOK.yaml`+`handler.py`) — это отдельный,
    чисто уведомительный `gateway/hooks.py::HookRegistry` (события через
    двоеточие: `agent:start`, `session:end`, ...; docstring модуля прямо
    говорит "Errors ... never block the main pipeline"). `pre_gateway_dispatch`/
    `pre_llm_call` с поддержкой `skip`/`rewrite`/`allow` дёргает ИСКЛЮЧИТЕЛЬНО
    `hermes_cli/plugins.py::PluginManager` через `~/.hermes/plugins/<name>/`
    (`plugin.yaml` с `hooks:` + `__init__.py` с `register(ctx): ctx.register_hook(...)`),
    и только если имя явно в `plugins.enabled` (opt-in по умолчанию —
    `hermes plugins enable <name>`). `HookRegistry.discover_and_load()` не
    проверяет имена событий на валидность — исправно "загрузил" плагин под
    несуществующими для себя именами `pre_gateway_dispatch`/`pre_llm_call`
    (лог "[hooks] Loaded hook ...") и НИКОГДА их не вызывал. LLM отвечала
    нормально всё это время — гейта просто не было, без единой ошибки в
    логах. Обнаружено только потому, что `tasks` в БД не росла после живых
    Telegram-тестов, хотя прямой curl на `/internal/inbound` работал.
    Исправлено переносом в `~/.hermes/plugins/helm-control/` + `hermes
    plugins enable helm-control`.
6b. **Колбэки `PluginManager` обязаны быть синхронными.**
    `PluginManager._invoke_hook_callback` делает ровно
    `return callback(**payload)` — ни проверки на корутину, ни await. Первая
    версия плагина (`async def handle`) после переезда в `~/.hermes/plugins/`
    молча создавала объект корутины, который никто не запускал
    (`RuntimeWarning: coroutine 'handle' was never awaited`); gateway/run.py
    получал этот объект как non-None `_result`, проваливал
    `isinstance(_result, dict)` и пропускал сообщение к модели тем же молчаливым
    образом, что и находка 6a. Исправлено: `handle`/`_on_pre_gateway_dispatch`
    — обычные `def`; уведомление в Telegram при недоступном CP — через
    `asyncio.get_running_loop().create_task(...)` (fire-and-forget: await
    внутри синхронного колбэка невозможен).
6c. **`event.user_id` пуст на реальных Telegram-сообщениях** (в этой версии
    Hermes) → `owner_id=""` → Control Plane отвечал `422 "owner_id: String
    should have at least 1 character"`. Заодно `event.message_id` — Python
    `int` (родной тип Telegram), а не `str`, которого ждёт
    `InboundMessage.external_message_id`. Исправлено: `owner_id` берётся из
    `source.chat_id`, если `event.user_id` пуст (в приватном чате Telegram
    `chat_id == user_id`, это не подмена identity); `external_message_id`
    явно приводится к `str()`.
6d. **Python-логи gateway-процесса под systemd буферизуются блоками** (нет
    TTY) — часть строк не долетала до `journalctl` до следующего рестарта,
    что несколько раз давало ложное впечатление "хук не сработал вообще".
    Исправлено: `Environment=PYTHONUNBUFFERED=1` через systemd drop-in
    (`/etc/systemd/system/hermes-gateway.service.d/unbuffered.conf`).
    Отдельно: INFO-уровневые логи самого Hermes (`pre_gateway_dispatch
    skip: reason=...`) не появлялись даже после этого фикса — вероятно,
    отфильтрованы уровнем логгера Hermes; для диагностики 6c плагин
    временно печатал причину напрямую в stdout (`print(..., flush=True)`,
    в обход `logging`) — оставлено в коде как постоянная диагностика на
    случай будущих отказов регистрации.

7. Плагин падал при чтении `/etc/helm/secrets/hermes_service_hmac`: сам
   файл был `0750 root:helm-secrets` (верно), но каталог `/etc/helm`
   — `0700 root:root`, без `x` для группы `helm-secrets` обход каталога
   невозможен (classic Unix: на каждый компонент пути нужен `x`).
   Исправлено точечно: `chgrp helm-secrets /etc/helm && chmod g+x /etc/helm`
   (только execute, без read — соседи `/etc/helm/ssh`, `/etc/helm/backup`
   не раскрываются). Пользователь `helm` также добавлен в группу
   `helm-secrets` (`usermod -aG`) — не ослабляет реальную границу доверия:
   `helm` и так имеет passwordless sudo, то есть уже эквивалентен root.
8. `HTTPS_PROXY`/`HTTP_PROXY` (обычные env-переменные) не влияют на
   Telegram-соединение Hermes — платформенный адаптер использует
   собственный сетевой слой, не generic httpx-детект прокси по env.
   Настоящий механизм — `TELEGRAM_PROXY` (отдельная переменная,
   `resolve_proxy_url("TELEGRAM_PROXY", ...)` в `gateway/platforms/base.py`)
   + `HERMES_TELEGRAM_DISABLE_FALLBACK_IPS=1` (пропускает DNS-over-HTTPS
   резолв запасных IP). Найдено чтением реального установленного исходника
   на сервере, не документации.
9. **Первое реальное сообщение упало**: `litellm.APIError: OpenrouterException
   - AsyncCompletions.create() got an unexpected keyword argument
   'reasoning_effort'`, одинаково на `helm-standard` и его fallback.
   Причина verified в исходнике Hermes: `agent.reasoning_effort` по
   умолчанию не задан (пустая строка) → `hermes_constants.py` резолвит
   это в `medium` с предупреждением и отправляет `reasoning_effort` как
   top-level kwarg через провайдер `custom`; закреплённая
   `litellm:main-v1.55.8` не принимает этот параметр на пути OpenRouter
   вообще ни при каком значении. Исправлено на стороне Hermes —
   `hermes config set agent.reasoning_effort none` (`~/.hermes/config.yaml`
   → `reasoning_effort: null`); живое сообщение сразу после этой правки
   прошло без ошибки. Дополнительно в `config/models/litellm.yaml` включён
   `litellm_settings.drop_params: true` (официальный механизм LiteLLM —
   тихо отбрасывать параметры, которые не поддерживает провайдер) как
   защита на будущее: другая модель/провайдер за `custom`-профилем Hermes
   может прислать другой незнакомый LiteLLM параметр. Точный участок кода
   Hermes, из-за которого `null` не долетает до запроса (в отличие от
   пустой строки, которая долетала как `medium`), построчно не
   верифицирован — фикс подтверждён эмпирически (живой ответ в Telegram),
   не вычитыванием каждой ветки провайдер-плагина `custom`.

**P4 — профили и virtual keys (§11.2, §15.3 п.7, §15.4) — закрыто:**

- [x] Профили `business`/`engineering`/`health`/`reviewer` созданы
      (`hermes profile create <name> --clone-from default`,
      `~/.hermes/profiles/<name>/`); `default` = `chief` (id профиля в
      Hermes не переименовывался — жёстко зашитое имя инструмента,
      см. отклонение выше).
- [x] По одному virtual key на профиль в LiteLLM (`/key/generate`, scoped
      к своему alias — не общий `litellm_master_key`):
      `chief→helm-standard`, `business→helm-standard`,
      `engineering→helm-code`, `health→helm-standard`,
      `reviewer→helm-review` (alias mapping — §11.2/§15.7). Ключи — в
      `/etc/helm/secrets/hermes_<profile>_litellm_key` (0640
      root:helm-secrets). Скрипт: `scripts/provision_hermes_profiles.sh`.
- [x] У всех четырёх worker-профилей `TELEGRAM_BOT_TOKEN`/
      `TELEGRAM_ALLOWED_USERS` обнулены в `.env` (§9.1: только `chief`
      имеет Telegram gateway; `--clone-from` копирует `.env` целиком,
      включая токен, который спека worker-профилям не разрешает).
- [x] Проверено вживую по отдельности: `engineering`/`reviewer` (разные
      alias, разные ключи) — `hermes -p <profile> -z '...'` → `pong`;
      `business`/`health` (тот же alias, что у chief) — аналогично;
      `chief` — реальное Telegram-сообщение после переключения с
      master key на свой scoped key («Тест 12» → нормальный ответ).

**Мелкая находка не первой важности:** собственное уведомление
`helm-control` в чат при недоступном Control Plane (`asyncio.create_task`,
fire-and-forget) не дошло до пользователя в живом fail-closed тесте — само
свойство fail-closed (сообщение не доходит до модели) при этом подтверждено;
стоит проверить, доживает ли таск до выполнения, когда обработчик события
уже возвращает управление гейтвею. Также: `hermes config set model.api_key`
печатает в свой вывод частично замаскированное значение ключа (первые/
последние 4 символа) — не полноценная утечка секрета (это UX самого CLI,
не наш код), но стоит иметь в виду при будущих ротациях через этот путь.

## Известные отклонения от live-server-first (ADR-017)

Первичная сборка Control Plane/Guardian/Panel велась офлайн в изолированной
сессии без исходящего TCP на порт 22 (см. ADR-017). С этого цикла работа
идёт live-server-first, как того требует §31.0: команды исполняются на
реальном сервере, владелец выполняет их из своего терминала.

## Открытые находки (P0 evidence, переносятся в docs/PRE-FLIGHT.md)

- `umask` интерактивной sshd-сессии — `0022` (корректно), но `scp -r`
  (sftp-subsystem) создал каталоги `/opt/helm/*` с правами `rwx---rwx`
  (world-writable) при первой передаче. Причина не установлена; временная
  мера — повторная нормализация прав (`chmod`) после каждой будущей `scp -r`
  на этот хост. Требует разбора конфигурации `sshd`/`sftp-server` до того,
  как на сервере появится непривилегированный пользователь.

## Правило кавычек для ssh с Windows/PowerShell (закреплено, не пересматривать)

Единственная рабочая схема для многословного удалённого фрагмента:
**внешние двойные кавычки PowerShell + внутренние одинарные кавычки bash**,
`` `$(...) `` (обратный апостроф перед `$`) для bash-подстановок, `\047` —
одинарная кавычка внутри printf. Пример:

```powershell
ssh -i "$HOME\.ssh\key" user@host "printf 'ROLE ... \047%s\047;' `$(sudo cat /path) | sudo cmd ..."
```

Не использовать: внешние одинарные PowerShell-кавычки с чем-либо многословным
внутри (кавычки/содержимое теряются), `-c` с многословным SQL/shell-текстом
(предпочитать `-f -` через stdin), любые вложенные `bash -c "..."`/`sh -c
"..."` с `$(...)` внутри (двойное вычисление до выполнения). Найдено ценой
нескольких проваленных попыток на P2 bring-up — не пересматривать без новых
доказательств.
