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

Docling-путь (эскалация) в коде есть и по API-сигнатуре корректен
(`DocumentConverter().convert(path).document.export_to_markdown()`,
подтверждено чтением реальной установленной библиотеки), но **не
проверен живым запуском**: Docling требует загрузки моделей layout/OCR
с huggingface.co при первом использовании — недоступно из песочницы
разработки (egress-прокси блокирует не-PyPI хосты организационной
политикой). Первый живой прогон на сервере (с настоящим интернетом)
покажет, работает ли эскалация так, как ожидается — это тестами не
покрыто, честно, не выдаётся за проверенное.

### Контейнеризация — `Dockerfile.worker`, `docker-compose.yml::helm-knowledge-worker`

Отдельный от `helm-core` контейнер (архитектурное решение владельца
29.08.2026): Docling тянет **~5.7GB** зависимостей (torch, OCR-модели —
измерено установкой в реальный venv), и при разборе скана/сложного PDF
может дать заметный скачок RAM. Тяжёлый разбор не должен иметь
возможность повлиять на процесс, отвечающий на живые вебхуки
MAX/Telegram (лимит `helm-core` — 768MB).

Найдено и учтено при сборке образа воркера:
- `torch` ставится ЯВНО через `--index-url https://download.pytorch.org/
  whl/cpu` — иначе `pip install docling` сам тянет обычный torch и берёт
  CUDA-сборку (лишние ~1.2GB `nvidia_*`-пакетов на сервере без GPU,
  подтверждено установкой без этого флага).
- `opencv-python` (транзитивная зависимость Docling/RapidOCR) на Debian
  slim требует системные библиотеки (`libgl1`, `libglib2.0-0`, `libsm6`,
  `libxext6`, `libxrender1`) — без них падает на первом же `import cv2`.

**Не проверено вживую**: сама сборка образа (`docker build`) — Docker
Hub недоступен из песочницы разработки (та же egress-политика). Первый
живой `docker compose build helm-knowledge-worker` на сервере — первая
реальная проверка, что apt-пакетов достаточно и торч ставится нужной
сборкой.

## Чего нет: attachments / spool (§14.5.1) — P8.5.7

Контракт спеки для входящих вложений Telegram/MAX:

```text
Telegram file message
→ chief gateway/plugin получает bytes/path через Bot API
→ запись в защищённый ingest spool (/opt/helm-state/knowledge-spool/,
  owner-only, bounded size, atomic rename)
→ SHA256 → atomic move в /opt/helm-knowledge/raw/<domain>/
→ register_file_for_ingest() → немедленное подтверждение владельцу
→ асинхронный parse (уже реализовано, см. выше)
```

Каталог spool создан (`scripts/knowledge-bootstrap.sh`), но ничего в
него не пишет — ни `helm-control` (Telegram), ни `/hooks/max` (MAX)
сегодня не обрабатывают вложения, только текст. Это следующий шаг
(P8.5.7): доставка файла от Telegram/MAX до `register_file_for_ingest()`
— сама функция и всё, что после неё, уже готовы и протестированы.

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
(8), `tests/test_knowledge_probe.py` (13, включая `ingest_text()`) —
157/157 зелёных вместе с остальным control-plane. Fixture-матрица
§30.8.5 полностью (сложный/табличный PDF, скан, аудио,
prompt-injection документ) недостижима без живой проверки Docling/
GigaAM на сервере — появится вместе с этим прогоном, не раньше.
