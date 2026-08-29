# HELM Knowledge — Ingest (ТЗ §14.5, §14.5.1, §14.6, §14.7)

Как материал попадает в базу знаний. Это документ о том, что РЕАЛЬНО
реализовано сегодня (29.08.2026) и что явно отложено — не пересказ
спеки как факта.

## Что есть сегодня: `ingest_text()`

`helm_core/knowledge/ingest.py::ingest_text(session, *, domain, text,
original_filename=None, sensitivity="internal", trust="extracted")`.

Единственный вход — прямой вызов из Python (нет HTTP-эндпоинта, нет
транспорта из Telegram/MAX). Делает три вещи:

1. **SHA256-дедуп** (§14.5: «повторный файл с тем же SHA256 не
   обрабатывается заново, связывается с существующим source»): хэш от
   `text.encode("utf-8")`, при совпадении с уже существующим
   `knowledge_sources.sha256` возвращает существующий source, не создаёт
   дубль.
2. **Разбиение на чанки по абзацам** (`\n\s*\n+`) — не структурные чанки
   Docling (таблицы/страницы), но детерминированно и достаточно для FTS.
3. **`to_tsvector('russian', chunk_text)` на стороне БД** для каждого
   чанка — конфигурация словаря живёт в Postgres, не дублируется в коде
   приложения.

`raw_path`/`source_path` в `knowledge_sources` записываются как
**ожидаемое** расположение файла (`/opt/helm-knowledge/raw/<domain>/
<sha256>.txt`, `.../sources/<sha256>.md`) — это НЕ файл, реально
записанный на диск. Запись RAW на диск — часть P8.5.2, ещё не сделана.

## Чего нет: parser router (§14.6)

Спекой описан двухступенчатый бесплатный роутер:

```text
Fast path  — MarkItDown: TXT/MD/HTML, DOCX, PPTX, XLSX/XLS, CSV, EPUB,
             MSG, ZIP, простые текстовые PDF
Quality path — Docling: сложные/табличные PDF, сканы, изображения,
             документы где fast path теряет структуру
```

Ни MarkItDown, ни Docling не установлены и не вызываются нигде в коде.
`ingest_text()` принимает уже готовый текст — извлечение текста из
реального файла (PDF, DOCX, книга по психологии и т.п.) сегодня не
реализовано вообще. Это P8.5.2: установка пакетов + прогон fixture-
матрицы документов на живом сервере (офлайн не делается — пакеты и их
поведение на реальных файлах нужно видеть вживую, не предполагать).

Parser quality gate (§14.6: непустой текст, доля распознанных
страниц, abnormal replacement characters, длина относительно исходника
→ если ниже порога, эскалация на Docling → если Docling тоже FAIL,
`status=NEEDS_REVIEW`) — тоже не реализован; `KnowledgeStatus` уже
содержит значение `NEEDS_REVIEW` в модели (добавлено заранее под этот
контракт), но ничто пока в него не переводит записи.

## Чего нет: attachments / spool (§14.5.1)

Контракт спеки для входящих вложений Telegram/MAX:

```text
Telegram file message
→ chief gateway/plugin получает bytes/path через Bot API
→ запись в защищённый ingest spool (/opt/helm-state/knowledge-spool/,
  owner-only, bounded size, atomic rename)
→ SHA256
→ atomic move в /opt/helm-knowledge/raw/<domain>/
→ создание knowledge_ingest_job
→ немедленное подтверждение владельцу
→ асинхронный parse
```

Каталог spool создан (`scripts/knowledge-bootstrap.sh`), но ничего в
него не пишет — ни `helm-control` (Telegram), ни `/hooks/max` (MAX)
сегодня не обрабатывают вложения, только текст. `knowledge_ingest_jobs`
— таблица есть (P8.5.1), но ни одна строка в неё не пишется никаким
кодом. Это P8.5.7, явно зависит от готового parser router (P8.5.2) —
раньше делать нечего.

## Чего нет: аудио — GigaAM (§14.7)

```text
audio/video → ffmpeg normalize → VAD/segmentation → GigaAM
→ transcript с таймстампами → transcript.md → chunks/index
```

Не установлено, не вызывается. Требует выбора конкретной версии GigaAM
(v3/e2e line, актуальной на дату установки) живым бенчмарком на
реальном русском аудио, на живом сервере — спека прямо запрещает
хардкодить версию до этого. См. `docs/KNOWLEDGE_MODELS.md`.

Контракт при появлении: `concurrency=1`, модель выгружается из RAM
после использования (не резидентна на 12GB VPS), Guardian не запускает
ASR при CRITICAL memory pressure, транскрипт получает `trust=extracted`
(не `owner_verified` автоматически).

## Тесты

`tests/test_knowledge_probe.py::test_ingest_same_text_does_not_duplicate`,
`test_ingest_splits_paragraphs_into_chunks` — покрывают ровно то, что
реализовано (дедуп, чанкинг). Fixture-матрица §30.8.5 (DOCX/PPTX/XLSX/
сложный PDF/скан/аудио/дубликат/prompt-injection документ) недостижима
без парсеров и ASR — появится вместе с P8.5.2/8.5.3, не раньше.
