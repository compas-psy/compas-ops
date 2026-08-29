# ADR-020. MAX-канал: Control Plane как клиент встроенного API Hermes

**Дата:** 29.08.2026 · **Статус:** сторона Control Plane реализована и
покрыта тестами (120 зелёных); включение API-сервера на сервере и
живой смоук — следующий шаг (`scripts/hermes-enable-runbook.md`)

**Ревизия того же дня.** Первая версия этого документа (тогда назывался
`ADR-020-max-bridge.md`) предлагала строить новый Hermes-side плагин
`max-bridge`: регистрировать «max» как платформу gateway, писать
`BasePlatformAdapter`, вбрасывать синтетические события в
`pre_gateway_dispatch`. Причина пересмотра — не смена мнения, а находка:
у Hermes уже есть то, что нужно, встроенным. Раздел «Что было предложено
раньше и почему отклонено» ниже сохраняет тот путь ради истории —
несколько часов разведки (`scripts/hermes-recon-*.sh`) ушли на то, чтобы
дойти до правильного вопроса.

## Контекст

ТЗ §10.2 предписывает: MAX-вебхук приходит напрямую в Control Plane,
который «использует Hermes Responses API с named conversation», чтобы
прогнать сообщение через того же chief-агента, что отвечает в Telegram,
и вернуть ответ через MAX Bot API.

**Первое заключение сессии (тем же днём, раньше) было ошибочным.**
`sudo ss -tlnp` не показал слушающего порта Hermes, а grep исходников на
буквальные слова спеки — "chief api", "named conversation" — ничего не
нашёл. Вывод «такого API не существует» был получен ПОИСКОМ ПО
ТЕРМИНОЛОГИИ СПЕКИ, а не по факту. У Hermes есть полноценный встроенный
OpenAI-совместимый API-сервер (`gateway/platforms/api_server.py`,
`Platform.API_SERVER`, порт по умолчанию 8642), в том числе:

```text
POST /v1/responses  — OpenAI Responses API format
                       (stateful via previous_response_id;
                        поле "conversation" — именованный разговор)
```

`conversation: "helm-max-owner"` в теле запроса — это и есть «Hermes
Responses API с named conversation» из §10.2, слово в слово, просто под
терминологией OpenAI Responses API, а не текста спеки. Сервер сам
резолвит имя разговора в `previous_response_id` последнего ответа;
Control Plane не хранит ничего, кроме самого имени (§10.2: «без
хранения полного MAX transcript в Control Plane»).

API был просто **выключен**: `_has_usable_api_server_key` требует
`API_SERVER_KEY` (≥16 символов) в `~/.hermes/.env`, которого не было —
`ss -tlnp` из первого захода корректно показал, что порт не слушает,
неверным был только вывод «его не существует».

## Решение

**Control Plane становится обычным HTTP-клиентом уже существующего API
Hermes.** Ни новой платформы, ни адаптера, ни плагина не требуется:

```text
MAX → POST /hooks/max (Caddy → helm-core)
  → проверка X-Max-Bot-Api-Secret (иначе 403)
  → проверка owner ID (иначе 403)
  → дедупликация + регистрация task (IngestService, channel="max")
      ├─ cross_channel_duplicate → МОЛЧА 200/202, Hermes не вызывается
      ├─ same_external_message_id → 200, повторно ничего не делаем
      └─ новая задача → 202 владельцу СРАЗУ, дальше в фоне:
  → POST 127.0.0.1:8642/v1/responses (Bearer API_SERVER_KEY)
      body: {"model": "hermes-agent", "input": text,
             "conversation": "helm-max-<owner_id>"}
      → chief-агент отвечает СИНХРОННО в теле этого же HTTP-ответа
  → OutboxMessage.enqueue(channel="max", recipient=chat_id, text=ответ)
      → outbox-dispatcher → POST platform-api2.max.ru/messages
```

Порядок шагов до вызова Hermes — ровно как в §10.1, и это не
формальность: вердикт дедупликации нужен ДО обращения к Hermes, иначе на
схлопнутом дубле chief ответил бы во второй раз на уже отвеченный вопрос.

**Ключевое отличие от первой версии ADR: вызов Hermes синхронный и
может идти минуты** (`_handle_responses` в api_server.py делает
`await self._run_agent(...)` внутри самого HTTP-запроса, без стриминга
в нашем случае) — там, где плагин-подход отвечал бы вебхуку сразу и ждал
ответ chief отдельным HTTP-коллбэком на `/internal/outbound`, здесь эту
роль играет **фоновая задача** (`BackgroundTasks` FastAPI): обработчик
`/hooks/max` регистрирует задачу и отвечает MAX `202` немедленно, а вызов
`/v1/responses` и постановка ответа в outbox происходят после того, как
HTTP-ответ уже ушёл, в отдельном потоке (синхронная функция под
`BackgroundTasks` выполняется в threadpool, не блокируя event loop).

**Молчаливое схлопывание** (решение владельца 29.08.2026, не изменилось):
при cross-channel дубле в MAX не уходит ничего. Ответ на этот вопрос
владелец получает в Telegram, куда написал первым.

Компоненты (все реализованы и протестированы — 29.08.2026):

1. **helm-core `/hooks/max`** — §10.1 целиком: секрет, владелец, дедуп,
   регистрация, планирование фоновой задачи. `helm_core/api/hooks.py`.
2. **`HermesBridge.deliver(owner_id, text) -> str`** — HTTP-клиент
   `/v1/responses`. `helm_core/hermes_bridge.py`.
3. **helm-core outbox-dispatcher** — общий воркер доставки (asyncio,
   внутри процесса helm-core, `--workers 1` → второго доставщика не
   возникает), ретраи с backoff, FAILED после лимита, доставленный текст
   стирается из очереди. `helm_core/dispatch.py`.

`/force` из §10.4 (явное «создай новую задачу») — снимает ровно одно
правило, cross-channel дедуп, и намеренно НЕ снимает идемпотентность по
`external_message_id` (переотправка апдейта транспортом — не повторная
просьба владельца). `helm_core/ingest.py::strip_force_prefix`.

## Что было предложено раньше и почему отклонено

Ради истории — несколько часов разведки (`scripts/hermes-recon.sh` —
`hermes-recon-4.sh`) ушли на выяснение, ПОЧЕМУ это не годится, прежде
чем нашёлся API-сервер:

- **Регистрация «max» как `Platform` через `platform_registry`** —
  технически возможно (`PlatformRegistry.register(entry, scope=)`
  существует), но требует полного `BasePlatformAdapter` (`connect`,
  `disconnect`, абстрактный `send`) и подключения в `self.adapters` на
  старте гейтвея — то есть Hermes сам начинает «владеть» подключением к
  MAX, что противоречит §10.1: единственный вход MAX — через Control
  Plane, Hermes отвечает только локально и пассивно.
- **`_dispatch_plugin_message_injection`** — многообещающее по имени, но
  требует УЖЕ существующей живой сессии (`lookup_by_session_key` +
  `_adapter_for_source(source) is not None`) — годится для проактивного
  сообщения В уже открытый разговор, не для первого сообщения из
  совершенно новой платформы, для которой ни сессии, ни адаптера нет.
- **Bundled platform-плагин** (`plugins/platforms/max/`, по образцу
  telegram/discord/slack) — архитектурно верный путь ДЛЯ ПОЛНОЦЕННОЙ
  двусторонней интеграции, но означает, что Hermes сам подключается к
  MAX (poll/webhook) — тот же конфликт с §10.1, что и выше, и на порядок
  больше кода, чем нужно для fallback-канала.

Итог: все три пути имели одну и ту же проблему — они делают Hermes
владельцем канала MAX. Правильный путь — Hermes НЕ знает про MAX вовсе;
он просто отвечает на вопрос через уже существующий, канало-независимый
API, а всей маршрутизацией (кто спросил, куда отвечать) занимается
Control Plane, как и предписывает §10.1.

## Ключевые инварианты

- 8642 (порт API-сервера Hermes) слушает только 127.0.0.1
  (`DEFAULT_HOST = "127.0.0.1"` в самом api_server.py) и явно входит в
  `NEVER_PUBLIC_PORTS` (`test_perimeter.py`) — без этого прямой публичный
  маршрут на него обошёл бы весь Control Plane целиком (регистрацию,
  дедуп, owner-проверку).
- Авторизация вызова — `Authorization: Bearer <API_SERVER_KEY>`, тот же
  ключ живёт в двух местах с одним значением: `/etc/helm/secrets/
  hermes_api_server_key` (Docker secret Control Plane) и
  `~/.hermes/.env` (host-side, читает сам Hermes) — по аналогии с уже
  устоявшимся паттерном `hermes_service_hmac`.
- Тексты сообщений не логируются ни в `/hooks/max`, ни в `HermesBridge`,
  ни в `dispatch.py` (§5.2 CLAUDE.md).
- MAX-путь не касается n8n нигде (P7 DoD «n8n-down test» проходит по
  построению — но тест всё равно выполняется вживую).
- Идентификатор владельца в MAX — **другое число**, чем в Telegram
  (мессенджеры не делят пространство id). Сверка входящего — с
  `max_owner_id`, задача регистрируется под каноническим `owner_id`
  (иначе `normalized_hash` §10.4 разошёлся бы между каналами навсегда).
- Окно cross-channel дедупа — 2 минуты по §10.4 (было 10 без ADR —
  исправлено).
- `conversation_name(owner_id)` даёт MAX СВОЙ, отдельный от Telegram
  разговор с тем же chief-агентом — §10.2 говорит про multi-turn ВНУТРИ
  MAX, не про слияние истории двух каналов в одну.

## Неподтверждённое: форма ответа `/v1/responses`

Запрос подтверждён по исходникам (`_handle_responses`,
`gateway/platforms/api_server.py`). Форма JSON-ответа — по общему
формату OpenAI Responses API (`output: [{type: "message", content:
[{type: "output_text", text: ...}]}]`), СЕЙЧАС НЕ ПРОВЕРЕНА ЖИВЬЁМ — до
включения API-сервера на этом конкретном сервере вызвать его было
нечем. `HermesBridge._extract_reply_text` парсит именно эту форму и
бросает понятную ошибку при расхождении (а не молчаливый KeyError).
`scripts/hermes-responses-diagnose.sh` вызывает `/v1/responses` напрямую
и печатает сырой JSON целиком — тем же приёмом, что раскрыл реальную
форму вызова MAX API (`scripts/max-diagnose-send.sh`, F-260829-21).

MAX API — уже подтверждено живьём (29.08.2026): `chat_id` идёт
query-параметром, отдельно от тела `{"text"}` (наследие TamTam Bot API).

## Порядок реализации

1. ✅ Оффлайн-сторона Control Plane: `/hooks/max` (фоновая задача),
   `HermesBridge` (клиент `/v1/responses`), outbox-dispatcher, `/force`,
   окно §10.4 — 120 тестов зелёные.
2. ⏳ Включить API-сервер Hermes: `API_SERVER_KEY` в `~/.hermes/.env` +
   тот же ключ в `/etc/helm/secrets/hermes_api_server_key`, рестарт
   гейтвея Hermes и пересборка helm-core — `scripts/hermes-enable-runbook.md`.
3. ⏳ Живой смоук `/v1/responses` напрямую (`hermes-responses-diagnose.sh`)
   — подтвердить форму ответа до первого реального сообщения через MAX.
4. ⏳ Живой DoD §30.5 + P7: реальный вопрос из MAX с ответом chief, дедуп
   Telegram+MAX в окне, `/force`, n8n-down, chief-down (остановить
   гейтвей Hermes — транспортное уведомление, как уже подтверждено
   29.08.2026 при отсутствующем API).
