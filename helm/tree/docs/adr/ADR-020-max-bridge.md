# ADR-020. MAX-канал: мост через новый Hermes-плагин `max-bridge`

**Дата:** 29.08.2026 · **Статус:** спроектировано, ждёт реализации
(владелец 29.08.2026 отложил MAX до отдельного захода; этот документ —
тот самый заход в части проектирования)

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

Поток входящего:

```text
MAX → POST /hooks/max (Caddy → helm-core)
  → проверка X-Max-Bot-Api-Secret по секрету (иначе 403)
  → POST 127.0.0.1:8090/v1/message (HMAC-подпись, как /internal/*)
      → плагин max-bridge: синтетический event(platform="max")
        → штатный gateway dispatch
          → helm-control.pre_gateway_dispatch РЕГИСТРИРУЕТ задачу в
            Control Plane (channel="max") — тот же fail-closed гейт и
            тот же IngestService-дедуп, что у Telegram, ноль новых
            путей регистрации
          → chief-агент отвечает
```

Поток исходящего:

```text
chief-ответ → gateway.adapters["max"].send(text, chat_id)
  → (внутри адаптера) POST /internal/outbound в Control Plane (HMAC)
    → OutboxMessage.enqueue(channel="max", recipient=chat_id)
      → НОВЫЙ outbox-dispatcher (фоновая задача helm-core):
        PENDING → POST platform-api2.max.ru/messages → SENT/ретрай
```

Компоненты (все три обязательны, других нет):

1. **Hermes-плагин `hermes/plugins/max-bridge/`** — HTTP-листенер
   строго на 127.0.0.1:8090 в daemon-потоке (стартует из
   `register(ctx)`), плюс фейковый adapter. Все колбэки — обычные
   `def` (F-260829-02: PluginManager не await'ит корутины);
   вбрасывание события в dispatch — через
   `asyncio.run_coroutine_threadsafe` в event loop гейтвея.
2. **helm-core `/hooks/max`** — проверка секрета вебхука, разбор
   `message_created`, форвард на 8090. Отвечает MAX'у 200 сразу после
   успешного форварда (регистрация задачи внутри dispatch — fail-closed
   уже там). Hermes недоступен → 503, MAX ретраит сам.
3. **helm-core outbox-dispatcher** — общий воркер доставки (asyncio,
   внутри процесса helm-core), первый канал — max. Ретраи с backoff,
   счётчик попыток, перевод в FAILED после лимита.

В объём этой же фазы входит `/force` из §10.4 (явное «создай новую
задачу», обходит правило дедупа №2) — сейчас его нет в `IngestService`.

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
  построению — but тест всё равно выполняется вживую).
- Дедуп-коллапс (та же мысль уже пришла через Telegram в 10-минутном
  окне): сообщение НЕ вбрасывается в dispatch (chief его уже получил),
  MAX-ответ не отправляется. Рекомендация — молчаливый коллапс: ответ
  владелец получает в Telegram, дублирующий «уже видел» в MAX — шум.
  ⚠ Это решение владельца, не агента — подтвердить до реализации.

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

## Порядок реализации (следующий батч)

1. Разведка на сервере (4 пункта выше) — вывод greps в чат, фиксация
   фактов здесь же.
2. Оффлайн: `/hooks/max`, `/internal/outbound`, outbox-dispatcher,
   `/force` — с тестами (fake MAX API, 74 существующих теста зелёные).
3. Плагин max-bridge + деплой (scp в `~/.hermes/plugins/`,
   `hermes plugins enable max-bridge`, рестарт гейтвея).
4. Живой DoD §30.5 + P7: секрет-тест вебхука, реальный вопрос из MAX с
   ответом chief, дедуп Telegram+MAX в окне, `/force`, n8n-down.
