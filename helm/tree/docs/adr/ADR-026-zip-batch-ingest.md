# ADR-026. ZIP batch ingest: безопасная распаковка + durable очередь + exactly-once финал

**Дата:** 30.08.2026 · **Статус:** код реализован, 250/250 тестов зелёных
локально, **ждёт живого деплоя и первой живой проверки** (ни один из 11
acceptance-тестов CONTINUE §10 не проверялся против реального Telegram/
MAX). Номер `ADR-026` — тот же, что называет сама спека v3.7 в
`## ADR-026 ZIP batch ingest`, переиспользование не требуется.

## Контекст

Спека v3.7 (`HELM_FINAL_v3.7_2026-08-30.md`, §14.4.0/§14.5.1-2/§14.6/
§14.7.6-7, P8.5.2.1) добавляет ZIP-архив как способ загрузить сразу
много документов в один домен. Прямая директива владельца 30.08.2026:
«не переделывая working single-document pipeline… реализуй ZIP
исключительно как durable batch/container layer перед существующим
child-ingest pipeline» — `implementation-state/V3.7-DELTA.md` фиксирует
это построчно, этот ADR — техническое решение.

## Решение

Три новых модуля в `helm_core/knowledge/`, ничего в уже работающем
`chat_intake.py`/`ingest.py`/`worker.py` не переписано (только точки
расширения — см. ниже):

```text
zip_safety.py     preflight()/extract_member() — валидация central
                  directory + потоковое чтение с реальным подсчётом
                  байт, БЕЗ shutil/zipfile.extractall()
batch_intake.py   stage_batch()/resolve_batch_domain()/retry_failed()/
                  cancel_remaining()/disable_created_sources()/
                  finalize_batch_if_terminal() — оркестрация
worker.py         +8 строк в process_job(): если job.batch_item_id
                  заполнен — sync_item_from_job() + finalize_batch_
                  if_terminal() в том же finally, что уже несёт
                  _notify_owner_of_result()
```

Схема — `knowledge_ingest_batches`/`knowledge_batch_items` (миграция
`f6617c6739ee`) + `knowledge_ingest_jobs.batch_item_id` (nullable FK,
единственное изменение существующей таблицы).

### Ключевое переиспользование, не изобретение

Каждый eligible-член архива идёт через **тот же самый**
`register_file_for_ingest()`, что уже обрабатывает одиночные вложения —
тот же SHA256-дедуп (D0 из §14.6 уже реализован, не строится заново),
тот же `KnowledgeIngestJob`, тот же `worker.py::process_job()`. Batch —
это то, что решает, КАКИЕ байты и в какой момент попадают в эту функцию,
а не параллельный pipeline.

`channel=None, recipient=None` при регистрации batch-члена — единственный
трюк для «no per-file push spam» (§14.5.2): `worker.py::
_notify_owner_of_result()` уже тихо no-op без них (написан для P8.5.7 24
часа назад, не тронут).

### Домен — статус batch, не отдельная pending-таблица

Одиночные вложения используют `KnowledgePendingAttachment` — отдельную
таблицу для "жду ответа о домене". Для batch этого не потребовалось:
`KnowledgeIngestBatch.status` сам проходит через `WAITING_DOMAIN`,
`resolve_batch_domain()` ищет batch с этим статусом на канале так же,
как `resolve_pending_domain()` ищет строку в другой таблице. Меньше
сущностей, тот же диалоговый паттерн.

### Извлечение: временное имя по item.id, финальное — по sha256

`extract_member()` не знает sha256 члена ДО того, как дочитает его
целиком (сам его считает по ходу чтения) — путь на диск определяется
ПОСЛЕ извлечения: временный файл `.batch-item-<item.id><ext>`,
переименование в `<sha256><ext>` внутри той же директории (гарантированно
один диск). Если файл с таким sha256 уже существует — временная копия
просто удаляется, `register_file_for_ingest()` найдёт существующий
source сам. Путь члена ВНУТРИ архива (`archive_member_path_original`)
остаётся только метаданными и никогда не становится путём на диске —
защита от zip-slip не зависит от того, безопасен ли конкретный путь: он
попросту не используется для адресации файла.

### Ретрай: два разных провала, два разных пути (найдено тестом, не спекой)

Первая версия `retry_failed()` звала `_process_item()` заново для любого
`FAILED`-члена — включая тот, что провалился на этапе **парсинга**
(`worker.py`), у которого `KnowledgeSource` уже существует. Повторный
вызов `register_file_for_ingest()` находил этот source по тому же
sha256 и отдавал `EXACT_DUPLICATE` вместо повторного разбора — тихо
"чинил" провал в неверную сторону. Исправлено: если `item.source_id`
уже заполнен, ретрай просто перевзводит существующий
`KnowledgeIngestJob` (`status=PENDING`), не трогая извлечение/регистрацию
вовсе; извлечение с нуля — только если `source_id` пуст (провал был до
регистрации source).

## Осознанно упрощено против буквы спеки (см. `V3.7-DELTA.md` целиком)

- `detected_mime` — `mimetypes.guess_type()` по расширению (stdlib), не
  magic-byte sniffing — ни один модуль кодовой базы сегодня не делает
  content-based MIME-детекцию, новая зависимость не оправдана.
- `domain_mismatch_candidate`, D1/D2 near-duplicate (`NORMALIZED_
  DUPLICATE`, `POSSIBLE_NEW_VERSION`) — не реализованы, требуют
  классификатора/эмбеддингов, которых нет; item может быть только
  `READY`/`EXACT_DUPLICATE` по точному SHA256, как и одиночный файл
  сегодня.
- `graph_status` — всегда `NOT_APPLICABLE` (Graphify не реализован,
  P8.5.6); финализация batch не ждёт несуществующей стадии.
- Panel «Архивы» — не в списке из 11 acceptance-тестов, отдельная
  задача после того, как сам pipeline подтвердится живьём.
- Backup/restore архива — новых строк в `backup.sh` не потребовалось:
  `/opt/helm-knowledge/raw-batches/<batch_id>/original.zip` (путь
  спеки дословно) уже внутри существующего `restic backup /opt/
  helm-knowledge`.

## Честно не проверено

Ни один из 11 acceptance-тестов CONTINUE §10 не проверялся против
реального Telegram/MAX — только против настоящего PostgreSQL через
`TestClient`/прямые вызовы (250 тестов, `parse_file()` подменён тем же
паттерном, что уже есть в `test_knowledge_worker.py`). `helm-control/
__init__.py` (Telegram-плагин) untestable локально, как и весь
предыдущий Telegram-код P8.5.7 — первая проверка только живым деплоем.
Реальный лимит Telegram Bot API на `getFile` (20MB, не наш собственный
`MAX_ARCHIVE_BYTES=1GB`) — задокументированное ограничение платформы,
не проверено живым архивом крупнее 20MB (ожидаемо не пройдёт через
Telegram вовсе, независимо от нашего кода — через MAX таких ограничений
на сегодняшний день не найдено).
