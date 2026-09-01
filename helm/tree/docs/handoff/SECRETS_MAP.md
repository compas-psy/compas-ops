# SECRETS_MAP — где живёт что (без значений)

§35 требует эту карту без самих секретов. Ни один секрет не хранится в
репозитории, коде или этом документе — только имя, назначение,
расположение и кто его заводит. Актуально на 31.08.2026 (грепом по
`docker-compose.yml`/`.github/workflows/`/`helm_core/config.py`/
`scripts/`, не по памяти).

## Два разных хранилища — не путать

1. **GitHub Secrets** (`Settings → Secrets and variables → Actions` в
   `compas-psy/compas-ops`) — читаются только раннером GitHub Actions,
   агент их не видит никогда (CLAUDE.md §5.4, устав §6).
2. **VPS host-side файлы** `/etc/helm/secrets/<name>` (0600 или 0640,
   `root:root` либо `root:helm-secrets`) — читаются на сервере, монтируются
   в контейнеры как Docker secrets (`docker-compose.yml`, блок `secrets:`)
   и оказываются ВНУТРИ контейнера под `/run/secrets/<name>`, откуда их
   читает `helm_core/config.py::read_secret()`. Путь `/etc/helm/secrets/`
   — путь ХОСТА, не контейнера; спутать эти два пути — типовая причина
   "секрет не найден" при первом заведении нового.

## GitHub Secrets (Actions)

| Имя | Для чего | Кто заводит |
|---|---|---|
| `VPS_SSH_KEY` | Приватный ключ ОТДЕЛЬНОЙ SSH-пары для `deploy.yml` — не тот ключ, что на машине владельца, чтобы отзывался независимо | Владелец, один раз |
| `VPS_KNOWN_HOSTS` | Вывод `ssh-keyscan -H 185.250.44.137` — без него пришлось бы отключить проверку хоста | Владелец (действие `keyscan` в `deploy.yml` печатает актуальное значение) |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | Уведомления в Telegram по итогам workflow (деплой, ночной цикл, борд) | Владелец, один раз |
| `PRODUCTS_READ_TOKEN` | Чтение репозиториев продуктов ночным циклом/бордом (`daily.yml`/`board.yml`/`implement.yml`) — вне HELM, уровень compas-ops | Владелец |
| `CLAUDE_CODE_OAUTH_TOKEN` | Запуск Claude Code раннером для ночного цикла/борда — вне HELM, уровень compas-ops | Владелец |

## VPS host-side файлы (`/etc/helm/secrets/`)

| Файл | Использует | Назначение |
|---|---|---|
| `postgres_password` | postgres, helm-core, воркер | Пароль роли `helm_app` (не `helm` — суперпользователь только для миграций) |
| `helm_database_url` | helm-core, воркер | Строка подключения к БД `helm` |
| `litellm_master_key` | litellm | Master-ключ LiteLLM-прокси |
| `litellm_database_url` | litellm | Отдельная БД LiteLLM (не `helm`) |
| `openrouter_api_key` | litellm | Единственный путь к платным моделям — только через LiteLLM, ничто другое в системе не хранит платный API-ключ напрямую |
| `telegram_bot_token` | helm-core (`app.state.telegram_bot_token`) | Owner chief bot в Telegram |
| `telegram_owner_id` | helm-core, `test_action_flow.sh` | Единственный `TELEGRAM_ALLOWED_USERS` для owner chief bot |
| `max_bot_token` / `max_webhook_secret` / `max_owner_id` | helm-core (MAX-канал) | Аналог трёх telegram-секретов выше, для MAX (ADR-014) |
| `hermes_service_hmac` | helm-core, `hermes-control` плагин | HMAC-подпись `/internal/*`-эндпоинтов между Control Plane и Hermes (§10.2) — ОБЯЗАН совпадать в обоих местах (host-файл Hermes и Docker secret Control Plane), иначе подпись не сойдётся |
| `hermes_api_server_key` | helm-core (`HermesBridge`), `hermes-control` плагин | Тот же принцип совпадения в двух местах, для API Hermes напрямую |
| `panel_auth_cookie_secret` | helm-core (Panel auth) | Подпись auth-cookie Panel |
| `knowledge_telegram_bot_token` / `knowledge_telegram_webhook_secret` | helm-core (Dedicated Knowledge Bot, ADR-029) | Пустой файл = вебхук fail-closed отклоняет всё — ожидаемое состояние до заведения бота через BotFather, не поломка |
| `n8n_database_password` / `n8n_encryption_key` | n8n | Отдельная БД n8n + шифрование хранимых n8n credentials |
| `forgejo_database_password` | forgejo | Отдельная БД Forgejo |
| `n8n_api_key` | `scripts/n8n-workflows.py` (не docker-compose secret — читается напрямую скриптом с хоста) | Экспорт/импорт workflow'ов n8n через API |
| `github_mirror_pat` | `scripts/forgejo-migrate.py` (не docker-compose secret) | GitHub Personal Access Token для зеркалирования Forgejo→GitHub (`0600 root:root`) |
| `hermes_<profile>_litellm_key` (по одному на профиль) | Hermes (через LiteLLM) | Per-profile ключ у LiteLLM-прокси, не сам `openrouter_api_key` — позволяет отзывать/квотировать по профилю независимо |
| `restic_password` | `backup.sh` (упомянут в `WORKPLAN.md`, единственная копия — не восстановить бэкап без него) | Шифрование резервных копий restic |

## Что проверить при заведении нового секрета

`STATUS.json` фиксирует живой урок (F-найдено при заведении
`knowledge_telegram_*`): сверяться с `ls -la /etc/helm/secrets/` на уже
рабочих записях перед созданием нового — не угадывать права по общей
интуиции безопасности. Де-факто конвенция: `root:root, 0600` для
секретов, которые читает только root-скрипт с хоста;
`root:helm-secrets, 0640` для тех, что монтируются в Docker secrets
(группе `helm-secrets` принадлежит процесс Docker, которому нужно их
прочитать при старте контейнера).

## Чего в этом документе нет

Ни одного значения секрета. Ни одного примера "как выглядит формат"
токена/ключа реального провайдера — только имена файлов и их
назначение, как и требует §35.
