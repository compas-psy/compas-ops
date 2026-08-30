# HELM Knowledge — Ingest (ТЗ §14.5, §14.5.1, §14.6, §14.7)

Как материал попадает в базу знаний. Это документ о том, что РЕАЛЬНО
реализовано сегодня (29.08.2026) и что явно отложено — не пересказ
спеки как факта.

## Путь 1: `ingest_text()` — готовый текст, без файла

`helm_core/knowledge/ingest.py::ingest_text(session, *, domain, text,
original_filename=None, sensitivity="internal", trust="extracted",
vault_root=...)`.

Прямой вызов из Python (нет HTTP-эндпоинта, нет транспорта). SHA256-дедуп
+ разбиение на чанки по абзацам + `to_tsvector('russian', ...)` на
стороне БД. `raw_path`/`source_path` — **ожидаемое** расположение файла,
ничего физически на диск не пишет. Использовался для смоук-тестов Probe
(P8.5.1/4/5) до появления реального парсинга файлов.

## Путь 2: `register_file_for_ingest()` + `worker.py` — реальный файл (P8.5.2)

Асинхронный pipeline для файла, уже лежащего на диске:

```text
register_file_for_ingest()          worker.py (отдельный процесс)
  SHA256, дедуп                       claim_next_job() — FOR UPDATE SKIP
  создать knowledge_sources           LOCKED, PENDING → RUNNING
  создать knowledge_ingest_jobs       process_job():
  (status=PENDING)                      parse_file() — MarkItDown/Docling
  → немедленный возврат                 chunk + index (§14.9)
                                         записать L1 SOURCE .md на диск
                                         DONE | NEEDS_REVIEW | FAILED
```

Реализовано и покрыто тестами (`tests/test_knowledge_worker.py`, 8
тестов): дедуп по SHA256 (файл с тем же содержимым не создаёт новый
job), захват задачи с блокировкой строки, все три исхода обработки
(успех → chunks + `status=DONE`; провал quality gate → `NEEDS_REVIEW`
без chunks, §14.6 «не создавать уверенные knowledge facts»; исключение
парсера → `FAILED` с текстом ошибки в `job.error`).

### Parser router (§14.6) — `helm_core/knowledge/parsers.py`

```text
Fast path (MarkItDown) → quality gate → OK? вернуть
                                       → провал? Quality path (Docling)
```

**Реализовано и проверено на реальных файлах** (не мок):
`tests/test_knowledge_parsers.py`, 11 тестов против настоящих
DOCX/PPTX/XLSX/CSV/TXT/PDF-фикстур (`tests/fixtures/knowledge/`), через
реально установленный MarkItDown (`markitdown[docx,pptx,xlsx,xls,pdf,
outlook]`, без `[all]` — §14.6 «only required extras», не тянет Azure
cloud SDK).

Quality gate (calibrated эмпирически, не «на глаз»):
- непустой текст (< 20 символов — провал)
- доля `�` (символ замены Unicode) выше 5% — провал
- **доля доминирующей буквы выше 25%** — найдено живым тестом: PDF,
  нарисованный шрифтом без нужных глифов (например Helvetica без
  кириллицы), даёт не `�`, а валидный, но полностью испорченный
  текст (реальный случай: кириллица схлопнулась в повторяющееся
  `'nnnnnnn'`). Замер на реальных документах дал 0.105–0.154, на
  сломанном — 0.346 — порог 0.25 лежит чисто между ними.

**Docling-путь (эскалация) подтверждён живьём 29.08.2026** на реальном
сервере (`scripts/knowledge-worker-smoke-test.sh`): PDF, извлечённый
MarkItDown с испорченным текстом (шрифт без кириллицы), корректно
провалил quality gate и эскалировал; Docling реально скачал модели
layout+table-structure с huggingface.co (~20 сек на первый прогон,
кэшируются на диске контейнера дальше), реально разобрал PDF (18 сек),
и — поскольку сам текст исходника действительно испорчен на уровне
шрифта, а не только на уровне парсера — его результат ТОЖЕ не прошёл
quality gate: `source.status=NEEDS_REVIEW`, chunks не созданы. Это
корректное, ожидаемое поведение (§14.6: «если Docling тоже FAIL —
source status NEEDS_REVIEW, не создавать уверенные knowledge facts»),
не баг — система честно отказалась выдать нечитаемый текст за факт.

### Контейнеризация — `Dockerfile.worker`, `docker-compose.yml::helm-knowledge-worker`

Отдельный от `helm-core` контейнер (архитектурное решение владельца
29.08.2026): Docling тянет **~5.7GB** зависимостей (torch, OCR-модели —
измерено установкой в реальный venv), и при разборе скана/сложного PDF
может дать заметный скачок RAM. Тяжёлый разбор не должен иметь
возможность повлиять на процесс, отвечающий на живые вебхуки
MAX/Telegram (лимит `helm-core` — 768MB).

**Задеплоено и подтверждено живьём 29.08.2026** на 185.250.44.137
(`scripts/knowledge-worker-deploy-runbook.md`). Найдено и исправлено по
пути (сборка образа и Docling недоступны из песочницы разработки — эти
находки возможны были только на живом сервере):

- `torch` + `torchvision` ставятся ОДНОЙ командой с ОДНОГО индекса
  (`--index-url https://download.pytorch.org/whl/cpu`) — раздельная
  установка (torch с этого индекса, torchvision как транзитивная
  зависимость docling с обычного PyPI) дала несовместимую пару сборок
  (разный ABI native-расширений) и роняла Docling `RuntimeError:
  operator torchvision::nms does not exist` при первом же импорте
  `transformers.AutoImageProcessor`.
- `opencv-python` (транзитивная зависимость Docling/RapidOCR) на Debian
  slim требует системные библиотеки (`libgl1`, `libglib2.0-0`, `libsm6`,
  `libxext6`, `libxrender1`) — без них падает на первом же `import cv2`.
- pydantic/pydantic-settings НЕ фиксируются вручную (см. выше) — то же
  правило применяется и здесь: любая ручная версия pydantic-экосистемы,
  поставленная ДО docling, рискует быть занижена относительно того, что
  реально требует docling-core.
- `process_job()` в `worker.py` изначально ловил исключения только
  вокруг `parse_file()` — сбой на любом шаге ПОСЛЕ (например, запись L1
  SOURCE на диск) улетал необработанным, валил весь процесс, транзакция
  откатывалась (job возвращался в `pending`), Docker поднимал контейнер
  заново — и тот немедленно падал на ТОЙ ЖЕ задаче: вечный краш-луп на
  одной плохой задаче вместо `FAILED` и перехода к следующей. Один
  try/except на всё тело `process_job()` — единственный правильный
  контракт.

## Attachments / spool (§14.5.1, P8.5.7) — MAX реализован, Telegram открыт

Двухшаговый диалог выбора домена (решение владельца 29.08.2026, спека не
задаёт UX для этого) — `helm_core/knowledge/chat_intake.py`, подробности
и обоснование — `docs/adr/ADR-021-knowledge-attachment-transport.md`:

**Три шага, не два** (решение владельца по итогам живого теста
29.08.2026 — изначально было два, третьего уведомления о завершении
разбора не было вовсе):

```text
1. файл → spool (stage_attachment) → KnowledgePendingAttachment (FIFO по
          created_at внутри channel) → меню доменов владельцу
2. следующее сообщение того же канала:
     номер/имя/псевдоним домена → resolve_pending_domain → перенос в
                        raw/<domain>/ → register_file_for_ingest()
                        → «Сохранено в «X». Разбор запущен...»
     "отмена"/cancel/нет → снять запись, удалить файл из spool
     что угодно ещё → меню повторяется, pending не трогается
3. worker.py, асинхронно, по завершении job'а →
     DONE → «Разбор «X» завершён — сохранено фрагментов: N»
     NEEDS_REVIEW → «Разбор «X» не удался — ...»
     FAILED → «Разбор «X» завершился ошибкой — ...»
```

Шаг 3 работает через тот же `outbox`, что и остальная доставка:
`worker.py::process_job()` (отдельный контейнер `helm-knowledge-worker`)
пишет строку в `outbox` напрямую (`KnowledgeIngestJob.recipient` — новая
колонка, alembic `03af17f40250`, заполняется `chat_intake.py` из
`inbound.chat_id`), а забирает и доставляет её фоновый цикл `helm-core`
(`_dispatch_loop`, опрос раз в 5 сек) — тот же процесс, что уже
доставляет обычные ответы chief и подтверждения P8.5.7. Никакой новой
инфраструктуры доставки не потребовалось, только общая таблица.
`ingest_text()`/тестовые пути `channel`/`recipient` не задают — для них
уведомление тихо не отправляется (уведомлять некого).

Пока есть неразрешённое вложение на канале — следующее текстовое
сообщение перехватывается диалогом шага 2 и не доходит до `register()`/
Probe/chief (осознанно, не баг: «отмена» снимает вложение, если владелец
на самом деле хотел спросить что-то другое).

**Короткие псевдонимы доменов** (тоже по итогам живого теста — набирать
`simpas/company` на телефоне неудобно): `company`→`simpas/company`,
`practice`→`simpas/practice`, `zapiski`→`simpas/zapiski`,
`moments`→`simpas/moments`, `marketing`→`psy-marketing`,
`docs`→`signalai-docs`. Показаны в меню рядом с полным именем. Домены,
и так однословные без `/`/`-` (`personal`, `health`, `ventures`,
`engineering`, `library`), псевдонима не получили — не нужен.

`simpas/zapiski` через этот диалог принудительно получает
`sensitivity=client_restricted` (§14.15: "not indexed into general
namespaces") — `probe()` обновлён исключать этот домен из общего поиска
по умолчанию тем же способом, что уже исключён `health`
(`test_zapiski_domain_excluded_from_general_query`).

**MAX — реализовано и покрыто тестами** (20 новых тестов: `tests/
test_knowledge_chat_intake.py` — движок диалога изолированно, `tests/
test_max_channel.py` — весь путь через реальный HTTP `/hooks/max`):
вебхук сам скачивает вложение (`channels/max.py::download_attachment`)
и передаёт в `stage_attachment()`. Повторная доставка вебхука дедуплена
отдельно от `IngestService.register()` (`ingest.py::
record_channel_event_once` — у вложения нет текста для обычного
`normalized_hash`-дедупа, и `register()` не должен вызываться для этого
сообщения вообще).

**Подтверждено живьём 29.08.2026**: `message.body.attachments[].payload.
url` — реальная форма MAX-вебхука совпала с ожидаемой с первого раза
(владелец отправил реальный PDF в MAX, меню доменов пришло корректно).

**Найдено и исправлено тем же живым тестом**: `resolve_pending_domain()`
падал `OSError: [Errno 18] Invalid cross-device link` на `os.replace()`
— `/opt/helm-state/knowledge-spool` и `/opt/helm-knowledge` на реальном
сервере оказались РАЗНЫМИ файловыми системами (предположение "тот же
физический диск" было неверным, ничем не подтверждённым на этапе
написания кода). Атомарный rename между точками монтирования невозможен
принципиально, не только правами. Исправлено: copy во временный файл НА
ЦЕЛЕВОЙ файловой системе (`raw_dir`) + `os.replace()` внутри неё (это уже
гарантированно один диск, значит атомарно) + удаление исходника из
spool только после успешного rename — та же гарантия §14.5.1 ("raw_path
либо не существует, либо содержит ПОЛНЫЙ файл"), работающая независимо
от топологии монтирования. Любой сбой на этом шаге теперь возвращает
статус `failed` (pending НЕ снимается, файл остаётся в spool, владелец
получает «попробуйте выбрать домен ещё раз» и может повторить попытку
без повторной отправки файла) вместо необработанного исключения и
500-й ошибки на вебхук.

**Telegram — не реализовано.** `hermes/plugins/helm-control/__init__.py`
работает синхронным колбэком внутри процесса Hermes gateway (не Control
Plane, не Docker) — неизвестно без чтения реального исходника, несёт ли
`event` что-то про вложение и доступен ли токен бота для его скачивания
(спека сама допускает оба исхода — §14.5.1, "smallest transport adapter"
как легитимный запасной путь, ADR-018 по нумерации спеки). Read-only
разведка — `scripts/knowledge-telegram-attachment-recon.sh`; код и
решение появятся в `ADR-021` после неё, не раньше.

Каталог spool (`scripts/knowledge-bootstrap.sh`) теперь `770 + setgid`
(было `700`) — пишут туда ДВЕ разные стороны под разными UID: Hermes
(хостовый процесс, для будущей Telegram-стороны) и контейнер `helm-core`
(для MAX, уже реализовано) — тот же паттерн, что уже применён к самому
Vault в P8.5.2 (`group_add` + setgid, не буквальный "ровно один UID").

## Чего нет: аудио — GigaAM (§14.7) — P8.5.3

```text
audio/video → ffmpeg normalize → VAD/segmentation → GigaAM
→ transcript с таймстампами → transcript.md → chunks/index
```

`ffmpeg` уже ставится в `Dockerfile.worker` (явное требование спеки,
поставлено заранее, чтобы не пересобирать образ дважды), но GigaAM не
установлен и не вызывается. Требует выбора конкретной версии (v3/e2e
line, актуальной на дату установки) живым бенчмарком на реальном
русском аудио — спека прямо запрещает хардкодить версию до этого. См.
`docs/KNOWLEDGE_MODELS.md`.

## Тесты

`tests/test_knowledge_parsers.py` (11), `tests/test_knowledge_worker.py`
(13, включая регрессию на краш-луп и 4 теста на уведомление о завершении
разбора — шаг 3), `tests/test_knowledge_probe.py` (15, включая
`ingest_text()` и исключение `simpas/zapiski` из общего поиска),
`tests/test_knowledge_chat_intake.py` (27, движок диалога изолированно,
включая cross-device rename и псевдонимы доменов) — 203/203 зелёных
вместе с остальным control-plane (включая MAX-вложения в `tests/
test_max_channel.py`). Fixture-матрица
§30.8.5 полностью (сложный/табличный PDF, скан, аудио,
prompt-injection документ) недостижима без живой проверки GigaAM на
сервере (Docling-часть уже подтверждена живьём выше) — появится вместе
с P8.5.3.
