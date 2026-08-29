# ADR-020. MAX-канал: мост через новый Hermes-плагин `max-bridge`

**Дата:** 29.08.2026 · **Статус:** сторона Control Plane реализована и
покрыта тестами (117 зелёных); плагин Hermes ждёт разведки на сервере

## Контекст

ТЗ §10.2 предписывает: MAX-вебхук приходит напрямую в Control Plane,
который «использует Hermes Responses API с named conversation», чтобы
прогнать сообщение через того же chief-агента, что отвечает в Telegram,
и вернуть ответ через MAX Bot API.

Проверено вживую 29.08.2026: такого API у Hermes **не существует**.
`sudo ss -tlnp` не показал ни одного слушающего порта Hermes; grep
исходников (`/home/helm/.hermes/hermes-agent`) на "chief api" /
"Responses API" / "named conversation" находит только код, которым
Hermes сам ходит в OpenAI/Codex как к провайдеру модели — другое
значение того же термина. §10.2 описывает функциональность, которую
нужно построить, а не подключить.

Что уже есть и переиспользуется без изменений:

- `helm-control` плагин (`hermes/plugins/helm-control/`): fail-closed
  регистрация каждого входящего сообщения в Control Plane
  (`pre_gateway_dispatch`) до LLM-вызова. Канал он берёт из
  `event.source.platform.value` — не захардкожен на Telegram.
- `IngestService` (`helm_core/ingest.py`): generic cross-channel дедуп
  (§10.4) — same-channel redelivery, cross-channel окно 10 минут,
  intentional repeat. Написан заранее, каналонезависим, протестирован.
- `OutboxMessage.enqueue()` (`helm_core/outbox.py`): exactly-once
  дедуп исходящих по `dedup_key`. Доставщика (dispatch worker) нет —
  его нужно написать в любом случае, для любого канала.
- Caddy route `handle /hooks/max` → 127.0.0.1:8080 — уже в Caddyfile.
- Реальный MAX Bot API (по документации, вживую не проверялось):
  входящий вебхук несёт заголовок `X-Max-Bot-Api-Secret` (в §10
  спеки — он же), тип события `message_created`; исходящее —
  `POST https://platform-api2.max.ru/messages`, токен бота в
  `Authorization`, JSON `{chat_id, text}`.

## Решение

**MAX оформляется как ещё одна «платформа» внутри Hermes gateway** —
новый плагин `max-bridge` регистрирует в `gateway.adapters`
минимальный adapter с `platform="max"` и вбрасывает синтетические
события в тот же dispatch-путь, которым идут Telegram-сообщения.
Никакого отдельного «Responses API» не строим: chief-агент, его
контекст и его named conversation достаются бесплатно, потому что для
gateway это просто сообщение из ещё одного мессенджера.

Поток входящего — порядок шагов ровно как в §10.1, и это не
формальность: вердикт дедупликации нужен ДО обращения к Hermes, иначе на
схлопнутом дубле chief ответил бы во второй раз на уже отвеченный вопрос.

```text
MAX → POST /hooks/max (Caddy → helm-core)
  → проверка X-Max-Bot-Api-Secret (иначе 403)
  → проверка owner ID (иначе 403)
  → дедупликация + регистрация task (IngestService, channel="max")
      ├─ cross_channel_duplicate → МОЛЧА 200, Hermes не вызывается
      ├─ same_external_message_id → 200, повторно ничего не делаем
      └─ новая задача ↓
  → POST 127.0.0.1:8090/v1/message (HMAC-подпись, как /internal/*)
      → плагин max-bridge: синтетический event(platform="max")
        → штатный gateway dispatch → chief-агент отвечает
```

**Молчаливое схлопывание** (решение владельца 29.08.2026): при
cross-channel дубле в MAX не уходит ничего. Ответ на этот вопрос владелец
получает в Telegram, куда написал первым; второе «я это уже видел» в
резервном канале — шум, а не сервис.

Поток исходящего:

```text
chief-ответ → gateway.adapters["max"].send(text, chat_id)
  → (внутри адаптера) POST /internal/outbound в Control Plane (HMAC)
    → OutboxMessage.enqueue(channel="max", recipient=chat_id)
      → НОВЫЙ outbox-dispatcher (фоновая задача helm-core):
        PENDING → POST platform-api2.max.ru/messages → SENT/ретрай
```

Компоненты (все три обязательны, других нет):

1. **helm-core `/hooks/max`** — §10.1 целиком: секрет, владелец, дедуп,
   регистрация, вызов chief. ✅ реализовано.
2. **helm-core outbox-dispatcher** — общий воркер доставки (asyncio,
   внутри процесса helm-core, `--workers 1` в Dockerfile → второго
   доставщика не возникает), первый канал — max. Ретраи с backoff,
   FAILED после лимита, доставленный текст стирается из очереди.
   ✅ реализовано.
3. **Hermes-плагин `hermes/plugins/max-bridge/`** — HTTP-листенер
   строго на 127.0.0.1:8090 в daemon-потоке (стартует из
   `register(ctx)`), плюс adapter с `send()`, который шлёт ответ chief
   в `/internal/outbound`. Все колбэки — обычные `def` (F-260829-02:
   PluginManager не await'ит корутины); вбрасывание события в dispatch —
   через `asyncio.run_coroutine_threadsafe` в event loop гейтвея.
   ⏳ ждёт разведки (см. ниже).

`/force` из §10.4 (явное «создай новую задачу») — ✅ реализовано в
`IngestService`: снимает ровно одно правило, cross-channel дедуп, и
намеренно НЕ снимает идемпотентность по `external_message_id`
(переотправка апдейта транспортом — не повторная просьба владельца).

### Два жёстких требования к плагину

Синтетическое событие проходит через `helm-control`
(`pre_gateway_dispatch`), который регистрирует ЛЮБОЕ входящее в Control
Plane. Для MAX задача к этому моменту уже заведена вебхуком, поэтому
плагин обязан подставить в событие такие поля, чтобы эта вторая
регистрация схлопнулась в ту же задачу, а не создала вторую:

* `event.message_id` = **тот же `mid`**, что пришёл в вебхуке → сработает
  правило дедупа №1 (`same_external_message_id`). Свой сгенерированный id
  здесь создаёт вторую задачу на то же сообщение;
* `event.user_id` = **канонический `owner_id`** (Telegram-число из
  `/etc/helm/secrets/telegram_owner_id`), НЕ MAX-id отправителя. Иначе
  `/internal/inbound` ответит 403 (не владелец), и helm-control
  fail-closed не пропустит сообщение к модели вовсе.

Оба требования проверяются одним живым сообщением: в БД должна появиться
ровно одна задача с `origin_channel="max"`.

## Ключевые инварианты

- 8090 слушает только 127.0.0.1 и попадает в `NEVER_PUBLIC_PORTS`
  (`test_perimeter.py`) вместе с проверкой, что Caddy его не проксирует.
- Оба плеча Control Plane ↔ плагин подписаны тем же HMAC-конвеем, что
  `/internal/*` (`X-Helm-Timestamp` + `X-Helm-Signature`, окно
  свежести; секрет — `/etc/helm/secrets/hermes_service_hmac`, Hermes
  работает на хосте и читает его напрямую, как helm-control).
- Тексты сообщений не логируются ни в одном из трёх компонентов
  (§5.2 CLAUDE.md: содержимое переписки владельца — не для логов).
- MAX-путь не касается n8n нигде (P7 DoD «n8n-down test» проходит по
  построению — но тест всё равно выполняется вживую).
- Идентификатор владельца в MAX — **другое число**, чем в Telegram
  (мессенджеры не делят пространство id). Поэтому: сверка входящего идёт
  с `max_owner_id`, а задача регистрируется под каноническим `owner_id`.
  Найдено тестом при реализации: без разделения вебхук отвергал бы
  самого владельца, а «починка в лоб» (регистрация под MAX-id) молча
  сломала бы §10.4 — `normalized_hash` считается вместе с owner_id, и
  хэши одного вопроса из двух каналов разошлись бы навсегда.
- Окно cross-channel дедупа — 2 минуты по §10.4. В коде стояло 10 минут
  без ADR (более ранний офлайн-проход); исправлено на значение спеки.

## Что нужно выяснить на сервере до кода (read-only разведка)

1. Класс события и его конструктор: чем Telegram-adapter создаёт
   `event` (поля `.text`, `.source.platform`, `.source.chat_id`,
   `.user_id`, `.message_id` — известны из helm-control).
2. Имя dispatch-функции в `gateway/run.py`, которую нужно вызывать для
   синтетического события, и как получить ссылку на работающий
   gateway/loop из `register(ctx)` (если ctx её не даёт — сташить из
   первого вызова хука, как fallback).
3. Интерфейс adapter'а: что обязан реализовать объект в
   `gateway.adapters[platform]` кроме `send(text=, chat_id=)`.
4. Enum платформ: расширяем ли (`source.platform.value == "max"`) или
   нужен собственный объект с `.value`.

## Последствия

- Chief-агент один, канала два — ровно то, что требует §10.1/§10.2,
  без второй копии контекста и без самодельного sync-протокола.
- Регистрация задач и дедуп остаются в одном месте (helm-control +
  IngestService) — MAX не добавляет второго пути в обход гейта.
- Появляется outbox-dispatcher — нужен и будущим каналам (Telegram-
  уведомления Panel, дайджесты), пишется один раз.
- Цена: max-bridge зависит от внутренних интерфейсов Hermes (event,
  dispatch, adapters) — при обновлении Hermes плагин проверяется
  первым. Смягчение: вся зависимость сосредоточена в одном файле
  плагина, контракт с Control Plane — чистый HTTP+HMAC.

## Неподтверждённое: точная форма вызова MAX API

По документации (два независимых источника, живьём не проверено —
`dev.max.ru` недоступен из среды агента, бота MAX ещё нет): база
`https://platform-api2.max.ru` (домен сменился 19.07.2026), токен в
заголовке `Authorization`, отправка `POST /messages` с JSON-телом
`{"chat_id", "text"}`. Риск: MAX унаследован от TamTam Bot API, где
`chat_id` шёл query-параметром. Если живой смоук вернёт 400 — правка в
одной строке `channels/max.py::_send_request`, остальное не затрагивается.

## Порядок реализации

1. ✅ Оффлайн-сторона Control Plane: `/hooks/max`, `/internal/outbound`,
   outbox-dispatcher, `/force`, окно §10.4 — 117 тестов зелёные,
   включая молчаливое схлопывание и отказ при недоступном chief.
2. ⏳ Разведка на сервере (4 пункта выше) — команды в
   `scripts/max-bringup-runbook.md`.
3. ⏳ Плагин max-bridge + деплой (scp в `~/.hermes/plugins/`,
   `hermes plugins enable max-bridge`, рестарт гейтвея).
4. ⏳ Живой DoD §30.5 + P7: секрет-тест вебхука, реальный вопрос из MAX с
   ответом chief, дедуп Telegram+MAX в окне, `/force`, n8n-down.
