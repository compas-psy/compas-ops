# ADR-021. Вложения Telegram/MAX: spool, двухшаговый диалог, транспорт

**Дата:** 29.08.2026, обновлено 30.08.2026 · **Статус:** MAX-сторона
задеплоена и подтверждена живьём (реальный PDF в MAX,
`docs/KNOWLEDGE_INGEST.md`); Telegram-сторона **реализована, ждёт
живого деплоя и первой живой проверки** (код готов, untestable
локально — `helm-control` вне пакета `helm_core`).

**Две доработки по итогам живого использования, задеплоены и
подтверждены живьём** (тот же день, после первого успешного файла):
(1) диалог стал ТРЁХшаговым — добавлено уведомление по завершении
асинхронного разбора (`worker.py`, через новую колонку
`KnowledgeIngestJob.recipient` + тот же общий `outbox`); (2) короткие
псевдонимы для доменов с `/`/`-` (`company`, `practice`, `zapiski`,
`moments`, `marketing`, `docs`) — `simpas/company` неудобно набирать на
телефоне. Владелец получил реальные три сообщения подряд на новый файл
и подтвердил, что алиас в меню сработал (не сразу заметил его в тексте
— формат `simpas/company (company)`, но нашёл при повторном чтении).

**Решение по итогам живого теста.** Отправка реального файла в MAX
случайно вскрыла, что чиф сегодня умеет читать вложения Telegram "на
лету" через свой обычный agentic-цикл (shell/OCR skills) — независимо
от этого pipeline'а. Владелец решил: итоговое поведение должно быть
ОДНИМ для обоих каналов — вложение ВСЕГДА уходит в базу знаний, чиф не
вызывается на вложение напрямую. Реализация Telegram-стороны P8.5.7
**заменит** нынешнее agentic-чтение файлов (не будет с ним
сосуществовать) — учитывать при разработке transport-адаптера ниже.

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

## MAX: реализовано, задеплоено, подтверждено живьём 29.08.2026

`/hooks/max` скачивает вложение сам (`channels/max.py::download_attachment`)
и передаёт байты в `stage_attachment()` — никакого нового транспорта не
требуется, вебхук и так уже в Control Plane. Дедуп повторной доставки
вебхука — отдельная функция `ingest.py::record_channel_event_once`
(не через `IngestService.register()`, у вложения нет текста для
`normalized_hash`, и `register()` не должен вызываться для этого
сообщения вообще).

**Форма вложения подтверждена живьём с первого раза**: `parse_attachment()`
разбирает `message.body.attachments[].payload.url` — форма,
документированная у TamTam-производных Bot API, `dev.max.ru` был
недоступен из egress-политики песочницы разработки (тот же класс
ограничения, что `huggingface.co` для Docling, P8.5.2), поэтому до
живого теста это оставалось предположением. Владелец отправил реальный
PDF ("Консультация эндокринолога") — форма совпала, меню из 11 доменов
пришло корректно.

**Найдено и исправлено тем же живым тестом**: `resolve_pending_domain()`
падал `OSError: [Errno 18] Invalid cross-device link` на `os.replace()`
— `/opt/helm-state/knowledge-spool` и `/opt/helm-knowledge` на этом
сервере оказались РАЗНЫМИ файловыми системами (комментарий кода "тот же
физический диск" был неподтверждённым предположением). Исправлено:
copy во временный файл на целевой файловой системе + `os.replace()`
внутри неё (гарантированно один диск — атомарно) + удаление исходника
из spool только после успешного rename; та же гарантия §14.5.1, не
зависящая от топологии монтирования. Сбой на этом шаге теперь даёт
статус `failed` (pending не снимается, файл остаётся в spool, владелец
может повторить выбор домена без повторной отправки файла) вместо
необработанного исключения и 500-й на вебхук.

## Telegram: реализовано, ждёт живого деплоя (30.08.2026)

Живая разведка (`scripts/knowledge-telegram-attachment-recon.sh`,
`-2.sh`, `-3.sh` — три захода, read-only, реальный исходник Hermes на
сервере) ответила на оба вопроса из первой версии этого раздела:

1. **`event` несёт вложение напрямую.** `MessageEvent`
   (`gateway/platforms/base.py`, `@dataclass`) — "normalized
   representation that all adapters produce" — поле `raw_message`
   несёт НАТИВНЫЙ объект `python-telegram-bot` (подтверждено чтением
   `_build_message_event()` в `plugins/platforms/telegram/adapter.py`:
   `MessageEvent(..., raw_message=message, ...)`). Тот же объект, на
   котором сам адаптер УЖЕ вызывает `await obj.get_file()` +
   `await file_obj.download_as_bytearray()` для собственного
   agentic-чтения чифом (строки ~9959–10195 файла на 30.08.2026,
   document/photo/voice/audio/video — идентичный набор типов).
   `MessageType` (enum: TEXT/PHOTO/VIDEO/AUDIO/VOICE/DOCUMENT/
   STICKER/COMMAND/LOCATION) тоже нашёлся в том же файле.
2. **"Smallest transport adapter" не понадобился вовсе** — раз `event`
   уже несёт полный объект с привязанным токеном бота (PTB связывает
   `get_file()` с ботом внутри самого объекта), отдельный HTTP-вызов
   `getFile`+download не нужен, никакого второго consumer'а апдейтов
   не заводится.

Единственная реальная сложность — не транспорт, а то, что `chat_intake.
py` (SHA256/spool/домен-диалог) живёт в процессе Control Plane, а
`helm-control` — вне его (свой venv на хосте Hermes) и не может звать
эти функции напрямую. Решение: два новых HMAC-подписанных HTTP-
эндпоинта в Control Plane, тот же паттерн, что уже у
`/internal/inbound`/`/internal/knowledge/probe`:

```text
POST /internal/knowledge/attachment/stage
  {channel, data_base64, original_filename, mime_type, caption}
  -> stage_attachment() -> {status, text: меню доменов}
POST /internal/knowledge/attachment/resolve
  {channel, reply_text, recipient}
  -> resolve_pending_domain() -> {status, text}
  status="not_pending" -> вызывающая сторона продолжает обычный путь
```

`helm-control/__init__.py`: `_on_pre_gateway_dispatch` проверяет
вложение (`_message_has_attachment(event.raw_message)`) РАНЬШЕ проверки
`event.text` — Telegram документы/фото обычно приходят с ПУСТЫМ `text`
(содержимое в отдельном поле `caption`), старая проверка `if not event.
text: return None` пропускала бы такое сообщение мимо гейта целиком.
При найденном вложении — скачивание (`_download_message_attachment`,
дословно тот же вызов, что уже в adapter.py) уходит в фоновую задачу
(`asyncio.get_running_loop().create_task(...)`, тот же fire-and-forget
паттерн, что уже у `_send_reply()`), а `{"action": "skip"}` возвращается
СИНХРОННО сразу — то же gating-свойство, которым Probe уже коротко
замыкает LLM на LOCAL_ANSWER, здесь применяется, чтобы чиф вложение не
увидел вовсе (решение владельца по итогам живого MAX-теста: одно
поведение для обоих каналов, Telegram-agentic-чтение вытесняется, не
сосуществует). Для обычных текстовых сообщений — резолв диалога
(`_resolve_attachment`) вызывается ДО `_register_task`, fail-open (как
Probe): недоступность Control Plane не блокирует сообщение.

**Честно не проверено** (untestable локально — `helm-control/__init__.
py` работает вне пакета `helm_core`, вне pytest): реальное поведение
`{"action": "skip"}` для медиа-сообщений (то же свойство, что уже
подтверждено для Probe/LOCAL_ANSWER, но не для вложений отдельно),
и сам факт, что скачивание через `event.raw_message` работает
идентично тому, что видит adapter.py. `docs/KNOWLEDGE_INGEST.md`
фиксирует это как первую живую проверку.
