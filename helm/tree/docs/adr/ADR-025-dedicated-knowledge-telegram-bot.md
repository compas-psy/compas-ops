# ADR-025. Dedicated Knowledge Telegram Bot + invite/identity verification

**Дата:** 30.08.2026 · **Статус:** код готов, 334/334 тестов зелёных,
**живой деплой не выполнялся** — реальный `KNOWLEDGE_TELEGRAM_BOT_TOKEN`
у владельца ещё не заведён (см. "Что нужно от владельца" ниже).

Спека (`HELM_FINAL_v3.8_2026-08-30.md`) предлагает номер `ADR-029` —
не переиспользуем: нумерация в этом репозитории идёт по факту принятых
решений (последний — `ADR-024`), не по нумерации спеки (см. тот же
прецедент в `ADR-021`).

## Контекст

v3.8 вводит `KNOWLEDGE_USER` — роль со своим Second Brain, без доступа
к остальному HELM. Директива владельца прямо запрещает пускать
secondary-пользователей через существующего owner chief bot
(`TELEGRAM_ALLOWED_USERS = SYSTEM_OWNER only`, не трогать) — им нужен
отдельный вход.

## Решение

**Отдельный Telegram bot token, отдельный вебхук, НАПРЯМУЮ в Control
Plane, минуя Hermes целиком.** Спека сама называет это "a transport
adapter, not a new reasoning service" (§9.0) — то есть архитектурно
проще, чем можно было предположить: не Hermes-плагин (как `helm-control`
для owner bot), а плоский Telegram Bot API webhook, тот же паттерн, что
уже есть у `/hooks/max`. Это даёт два практических выигрыша: (1) полностью
тестируется как обычный FastAPI-роутер (`test_knowledge_telegram_hook.py`,
16 тестов), в отличие от Telegram-стороны owner-путей (`helm-control`,
вне пакета `helm_core`, untestable локально); (2) KNOWLEDGE_USER не
может дойти до Hermes/OpenRouter/LiteLLM НЕ ПОТОМУ, ЧТО рантайм-проверка
это запрещает, а потому, что этот модуль их попросту не импортирует —
"never reaches Hermes by construction" в буквальном, не переносном
смысле.

Отдельный роутер `api/hooks_knowledge_telegram.py`, не расширение
`api/hooks.py` (тот явно объявляет себя MAX-специфичным в собственном
докстринге, и вся его логика зашита на порядок MAX-специфичных
проверок) — разделение по файлам, то же, что между `internal.py`/
`panel.py`/`auth.py`.

### Идентичность: `from.id`, не введённый вручную chat_id

Канонический принцип — Telegram `from.id` (§14.3 "Owner-entered chat
IDs are not sufficient proof of identity"). `chat.id` сохраняется
(`knowledge_channel_identities.external_chat_id`) только для доставки
ответа через outbox, никогда не используется для решения о доступе.

### Onboarding: одноразовый инвайт, хэш в БД

`knowledge/onboarding.py::create_invite()`/`consume_invite()` — та же
дисциплина, что уже есть у `PanelEnrollmentToken` (`api/auth.py`):
`secrets.token_urlsafe(32)` (256 бит энтропии) один раз показывается
владельцу, в БД — только `sha256(token)`. Deep link:
`https://t.me/<bot_username>?start=kb_<raw_token>`.

Порядок проверок в `consume_invite()`: revoked → used → expired →
`expected_external_user_id` mismatch → "тот же Telegram-аккаунт уже
привязан к другому активному пользователю" (проверка ПЕРЕД записью, не
полагаемся на UNIQUE constraint и обработку исключения — читаемее и не
требует savepoint вокруг ожидаемого сбоя).

### Owner-триггер инвайта: internal API, не Panel

Панель (P8.6.5, "Система → Пользователи") не реализована в этом заходе.
Спека сама допускает промежуточное состояние ("implementation continues
fully... activation remains owner-interactive pending") — вместо
временной небезопасной лазейки через owner chief bot (спека это прямо
запрещает: "no temporary insecure sharing through chief bot") сделан
`POST /internal/knowledge/users/invite`, тот же HMAC service-auth
паттерн, что у всех internal-эндпоинтов, вызывается вручную/скриптом
владельца до появления Panel UI.

### Suspended vs unknown: разные сообщения, одно решение о доступе

`resolve_active_user_by_identity()` — единственная функция, решающая
"есть доступ или нет" (возвращает `None` и для truly-unknown, и для
suspended/deleted). `find_user_by_identity()` — БЕЗ фильтра по статусу,
используется исключительно для точного текста ответа ("доступ
приостановлен" вместо общего "нет доступа") — разделение специально,
чтобы UX-удобство никогда не могло случайно ослабить сам access-check.

### Не в объёме этого захода (явно, не тихо)

- **Файлы/ZIP/голос для KNOWLEDGE_USER.** Нужен собственный Telegram
  file-download по raw HTTP (`getFile` + `GET /file/bot<token>/...`) —
  этот вебхук получает голый JSON, не объект `python-telegram-bot`,
  которым пользуется `helm-control` для owner-стороны. Вебхук отвечает
  честным "пока не поддерживается" на любое вложение, не молчит.
- **Recall Micro-Memory через `probe()`.** `try_remember()` пишет и
  индексирует память корректно и tenant-scoped; обычный вопрос
  ("Дай мне ссылку про титры") сегодня ищет только `KnowledgeChunk`
  (документы), не `KnowledgeMemory` — отдельная, не начатая задача,
  см. `V3.8-DELTA.md`.
- **Knowledge-administration команды** (§14.16, "Забудь это"/"Верни в
  память"/...) — не реализованы ни для одной роли, включая владельца.
- **Panel-приглашение/passkey secondary-пользователя** (§14.3 "Panel
  onboarding for KNOWLEDGE_USER") — ждёт P8.6.5.

## Что нужно от владельца

Реальный бот ещё не заведён — без него `KNOWLEDGE_TELEGRAM_BOT_TOKEN`/
`KNOWLEDGE_TELEGRAM_WEBHOOK_SECRET` резолвятся в пустую строку
(`read_secret(..., "")`), и вебхук fail-closed отклоняет всё (пустой
секрет не совпадёт ни с каким заголовком). Точный шаг:

1. У [@BotFather](https://t.me/BotFather) — `/newbot`, дать имя и
   `@username` (например, `cmpas_knowledge_bot`); получить токен.
2. Положить токен в `/etc/helm/secrets/knowledge_telegram_bot_token`
   (0600 root:helm-secrets, тот же принцип, что у остальных секретов).
3. Сгенерировать случайный webhook-секрет (например,
   `openssl rand -hex 32`), положить в
   `/etc/helm/secrets/knowledge_telegram_webhook_secret`.
4. Задать `HELM_KNOWLEDGE_TELEGRAM_BOT_USERNAME=<username без @>` в
   окружении `helm-core` (публичное имя, не секрет — нужно только для
   сборки deep-link владельцу).
5. `setWebhook` на реальный публичный URL (`https://helm.cmpas.ru/hooks/
   knowledge-telegram` через Caddy — тот же периметр, что уже пропускает
   `/hooks/max`) с `secret_token`, равным значению из шага 3.

До этого шага реализация не блокируется (тесты идут против сконструированного
FastAPI-приложения, не против реального Telegram) — только живая проверка
ждёт этого владельческого шага, тот же принцип, что уже применялся к
`KNOWLEDGE_TELEGRAM_BOT_TOKEN` v3.7/v3.8 в целом.
