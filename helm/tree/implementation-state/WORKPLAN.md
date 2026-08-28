# WORKPLAN — HELM v3.3

Живой файл состояния реализации (ТЗ §31.0). Обновляется оркестратором после
каждой значимой задачи — это заменяет необходимость владельцу писать
«продолжай».

## Текущая фаза: P2 — Control Plane (ядро подтверждено на живом сервере, Caddy/TLS ещё не поднят)

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
- [ ] firewall (22/80/443)
- [x] Docker + docker-compose plugin, daemon.json (bounded logs), hello-world подтверждён
- [ ] Caddy + TLS — **отложено до P2**: Caddyfile зависит от helm-core (service_healthy) и panel/dist, которых ещё нет
- [x] B7 подтверждён владельцем — доступ к консоли/rescue хостера есть
- [x] password-login и root SSH отключены (`10-helm-hardening.conf`, reload проверен свежим подключением)
- [ ] firewall (22/80/443)
- [ ] bounded journald logs
- [x] Docker + docker-compose plugin, daemon.json (bounded logs), hello-world подтверждён
- [ ] Caddy + TLS — **отложено до P2**: Caddyfile зависит от helm-core (service_healthy) и panel/dist, которых ещё нет

## P2 — Control Plane на живом сервере

- [x] `alembic upgrade head` применён к реальной БД: 17 таблиц (16 + `alembic_version`)
- [x] `post-migration.sql` применён: append-only lockdown на `task_events`, права `helm_app`
- [x] `helm-core` поднят и подтверждён живым: `GET /healthz` с хоста → `200` (не только внутренний
      Docker healthcheck — тот проходил и до фикса, см. находку ниже)
- [ ] Caddy + TLS — предпосылки изменились с прошлой записи: `helm-core` теперь реально healthy,
      `panel/dist` уже на сервере (пришёл вместе с деревом). Новый известный пробел —
      `/var/lib/helm-guardian/public-status.json`, на который у Caddy bind-mount: Guardian на
      сервере ещё не установлен (P5 live), файла нет — Docker создаст на его месте пустой каталог.
      Решение о том, поднимать ли Caddy/TLS сейчас с этим пробелом, за владельцем.

**Найдено и исправлено на этом bring-up:** `uvicorn --host 127.0.0.1` слушал loopback самого
контейнера, а не хоста — Docker healthcheck (исполняется внутри namespace контейнера) показывал
`healthy`, но `curl` с хоста получал connection refused. Тот же класс ошибки был и в `Caddyfile`
(`reverse_proxy 127.0.0.1:PORT` предполагает loopback хоста) — без `network_mode: host` у `caddy`
получил бы то же connection refused при первом запросе. Исправлено: `--host 0.0.0.0` в
`control-plane/Dockerfile`, `network_mode: host` у `caddy` в `docker-compose.yml`.

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
