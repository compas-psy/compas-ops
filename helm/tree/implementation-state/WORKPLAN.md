# WORKPLAN — HELM v3.3

Живой файл состояния реализации (ТЗ §31.0). Обновляется оркестратором после
каждой значимой задачи — это заменяет необходимость владельцу писать
«продолжай».

## Текущая фаза: P3 — LiteLLM (P1 и P2 закрыты на живом сервере, включая отложенный Caddy/TLS)

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
