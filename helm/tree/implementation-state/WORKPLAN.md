# WORKPLAN — HELM v3.3

Живой файл состояния реализации (ТЗ §31.0). Обновляется оркестратором после
каждой значимой задачи — это заменяет необходимость владельцу писать
«продолжай».

## Текущая фаза: P4 — Hermes (установлен, подключён к LiteLLM, реальный сквозной ответ получен; профили/Telegram/helm-control плагин ещё не сделаны)

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

**Осталось до конца P4:**
- [ ] Профили `chief`/`business`/`engineering`/`health`/`reviewer` (§11.2)
- [ ] Virtual keys LiteLLM на профиль вместо общего master key (§15.3 п.7, §15.4)
- [ ] Telegram gateway только у `chief` (§9.1) — нужен `TELEGRAM_BOT_TOKEN`
      (есть в `/root/helm-bootstrap` на сервере) + `python-telegram-bot`
      (сейчас не установлен, `hermes doctor` пометил как optional/missing)
- [ ] Плагин `helm-control`: hook `pre_gateway_dispatch` (регистрация задачи
      в Control Plane ДО первого LLM-вызова) + `pre_llm_call` (короткий
      trusted-контекст `HELM_TASK_ID`/`DOMAIN_HINT`) — §9.3
- [ ] A-DoD п.2-3: задача регистрируется в CP до LLM-вызова; при недоступном
      CP Hermes не исполняет задачу (fail-closed)

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
