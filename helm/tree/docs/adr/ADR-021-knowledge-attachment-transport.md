# ADR-021. Вложения Telegram/MAX: spool, двухшаговый диалог, транспорт

**Дата:** 29.08.2026 · **Статус:** MAX-сторона реализована и покрыта
тестами (191 зелёных, `docs/KNOWLEDGE_INGEST.md`); Telegram-сторона
**открыта** — ждёт живой разведки (`scripts/knowledge-telegram-attachment-
recon.sh`, `scripts/knowledge-attachment-deploy-runbook.md` шаг 4).

**Про номер.** ТЗ v3.4 §14.5.1 прямо называет этот документ `ADR-018`
("If current stable Hermes plugin hook cannot expose/download attachment
safely... Record exact solution in ADR-018"). `ADR-018-single-agent.md`
уже существует — принят 27.08.2026, другая, не связанная тема
(однопоточный режим исполнения этой сессии). Не нашли при сверке
`V3.4-DELTA.md` этим же заходом. Не переиспользуем и не переименовываем
существующий принятый ADR — берём следующий свободный номер (021),
этот блок — единственная связь с формулировкой спеки, чтобы её не
потерять при поиске по "ADR-018".

## Контекст

§14.5.1: вложение владельца должно сохраняться ДО любого парсинга/LLM.
Два входа с разной архитектурой:

- **MAX**: `POST /hooks/max` — уже в Control Plane, вебхук сам может
  скачать вложение синхронно (в пределах времени ответа на вебхук).
- **Telegram**: `hermes/plugins/helm-control/__init__.py` — синхронный
  колбэк `pre_gateway_dispatch(event, gateway)` ВНУТРИ процесса Hermes
  gateway (не Control Plane, не Docker), уже читает `event.text`/
  `event.user_id`/`event.message_id`/`event.source`, но не проверено,
  даёт ли `event` доступ к вложению (`document`/`photo`/`file_id`) или к
  боту/токену для его скачивания.

Домен для файла спекой не задан никаким конкретным UX — реализуется
конвенция, а не буквальная формулировка ТЗ.

## Решение: двухшаговый диалог (владелец, 29.08.2026)

Три варианта были на столе: (a) префикс домена в подписи к файлу, (b)
двухшаговый диалог, (c) не выбирать вообще. Владелец выбрал (b) —
естественнее в переписке на телефоне, домен не нужно помнить заранее.

```text
файл → spool (helm_core/knowledge/chat_intake.py::stage_attachment)
     → KnowledgePendingAttachment (FIFO по created_at внутри channel)
     → меню доменов владельцу (format_domain_menu)
следующее сообщение того же канала:
  номер/имя домена → resolve_pending_domain → atomic move в raw/<domain>/
                    → register_file_for_ingest() → подтверждение
  "отмена"/cancel/нет → снять запись, удалить файл из spool
  что угодно ещё      → меню повторяется, pending не трогается
```

Пока есть неразрешённое вложение на канале, СЛЕДУЮЩЕЕ текстовое
сообщение на этом канале перехватывается диалогом и не доходит до
`register()`/Probe/chief — это осознанное поведение диалога, не баг:
владелец может снять вложение словом «отмена», если хотел спросить
что-то другое.

`simpas/zapiski`, выбранный диалогом, принудительно получает
`sensitivity=client_restricted` (§14.15: "not indexed into general
namespaces") — `probe()` (`_lexical_search`) обновлён исключать этот
домен из обычного поиска по умолчанию, тем же способом, что уже
исключён `health`.

## MAX: реализовано, форма вложения не подтверждена живьём

`/hooks/max` скачивает вложение сам (`channels/max.py::download_attachment`)
и передаёт байты в `stage_attachment()` — никакого нового транспорта не
требуется, вебхук и так уже в Control Plane. Дедуп повторной доставки
вебхука — отдельная функция `ingest.py::record_channel_event_once`
(не через `IngestService.register()`, у вложения нет текста для
`normalized_hash`, и `register()` не должен вызываться для этого
сообщения вообще).

**Не подтверждено живьём**: `parse_attachment()` разбирает
`message.body.attachments[].payload.url` — форма, документированная у
TamTam-производных Bot API, но `dev.max.ru` недоступен из
egress-политики песочницы разработки (тот же класс ограничения, что
`huggingface.co` для Docling, P8.5.2). Расхождение НЕ уронит вебхук:
`MaxAttachmentUnsupported` ловится, лог несёт имена полей payload (не
значения), владелец получает нейтральное «не смог скачать» вместо
зависшего запроса. Первый реальный файл либо подтвердит форму, либо
даст ровно то, что нужно для одного точечного патча — не новый цикл
гадания.

## Telegram: открыто

`helm-control/__init__.py` синхронный (см. его собственный docstring —
`async def` там уже один раз незаметно проглотил реальные сообщения,
F-260829, до перехода на `PluginManager`). Курутина `bot.get_file()`/
скачивание — тоже асинхронные в `python-telegram-bot`/`aiogram` —
здесь их напрямую не вызвать без `asyncio.get_running_loop()` и того же
паттерна fire-and-forget, что уже есть у `_send_reply()`, либо без
существенной переработки колбэка. Кроме того, неизвестно (без чтения
реального `event`-класса), несёт ли `event` вообще что-то про вложение,
или его нужно доставать из отдельного «сырого» апдейта.

Ничего не реализовано и не гадается — `scripts/knowledge-telegram-
attachment-recon.sh` (read-only) должен ответить на два вопроса ДО
кода:

1. Есть ли в `event` (или в объекте, из которого gateway его собирает)
   что-то про document/photo/file_id — тогда путь такой же прямой, как
   у MAX, разница только в асинхронности вызова.
2. Если нет — доступен ли `TelegramAdapter` токен бота изнутри плагина
   (`gateway.adapters[...]`, как уже используется для `_send_reply()`)
   — тогда решение по спеке буквально: "smallest transport adapter that
   uses the same bot token and owner allowlist without starting a
   competing Telegram update consumer" — HTTP-вызов `getFile`+download
   напрямую (`urllib`, тот же стиль, что уже у `helm-control`), не
   отдельный консьюмер апдейтов Telegram.

Решение и код появятся здесь после разведки, не раньше.
