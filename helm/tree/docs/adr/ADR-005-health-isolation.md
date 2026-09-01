# ADR-005. Health isolation: generic public source envelope + security-scope private payload schemas

**Дата:** 31.08.2026 (логическое разделение) / 01.09.2026 (физическая
Postgres-изоляция) · **Статус:** реализовано в коде и покрыто тестами;
живой прогон `scripts/setup-health-role.sh` на боевом сервере (создание
пароля роли `helm_health`, таблиц `health.*`) ещё не выполнен — это
последний шаг, не архитектурная неопределённость. До его прогона
health-путь работает в fail-open режиме (см. «Деградация» ниже),
поведение не меняется относительно предыдущего состояния этого ADR.

## Контекст

§14.15/§4.5/§6.5 требуют для домена `health` отдельную схему/роль
Postgres — изоляцию сильнее, чем колонка в общей таблице. Первый заход
(31.08.2026) дал только логическое исключение: `KnowledgeDomain.HEALTH`
жёстко исключён из общего (без явного домена) поиска (`probe.py`), но
health-строки физически лежат в тех же таблицах `public.*`, что и любой
другой домен того же пользователя. Это было зафиксировано здесь же как
сознательный, явно не выданный за полный, пропуск.

Разбор задачи P12 («Postgres-схема/роль для health», ROADMAP-TO-DONE.md
Блок 3) обнаружил реальную архитектурную развилку: `public.knowledge_
sources`/`public.knowledge_ingest_jobs` — общий конверт и общая очередь
для ВСЕХ доменов, с хардкодной FK-связью (`knowledge_ingest_jobs.
source_id → knowledge_sources.id`) и общей fair-queue/retry-логикой
воркера (`worker.py`). Наивное «переехать health целиком в отдельную
схему» сломало бы эту связь или потребовало бы дублировать весь
queue/retry-контур ради одного домена — оба варианта хуже, чем
изоляция, которую они должны обеспечивать.

## Решение

**Generic public source envelope + security-scope private payload
schemas.** Единый конверт (`public.knowledge_sources`) и единая очередь
(`public.knowledge_ingest_jobs`) остаются общими для всех доменов —
никакого второго retry/fair-queue/backpressure контура специально для
health. Для `security_scope=health` конверт хранит ТОЛЬКО нейтральные,
не идентифицирующие документ поля (id, `knowledge_user_id`, `domain`,
статусы, `sha256`, `raw_path`/`mime_type`/`parser` — хэш-производное имя
файла и технические метаданные, см. ниже почему они не чувствительны).
Всё, что физически идентифицирует ЧЕЛОВЕКА и его состояние — имя файла,
текст, чанки, relations — уходит в отдельную схему `health`, отдельную
Postgres-роль `helm_health`, недоступную `helm_app`/chief вообще:

```
upload → public source envelope → public ingest job
       → worker видит security_scope=health
       → переключается на health-scoped соединение (роль helm_health)
       → читает/пишет health private metadata + chunks + relations
       → конверт получает только нейтральный статус READY/FAILED
```

### Почему `raw_path`/`mime_type`/`parser` остаются в public

Наивное прочтение спеки — перенести и их тоже. Проверка `chat_intake.py`
показала, что `raw_path` строится как `{vault_root}/raw/{domain}/
{sha256}.<ext>` — хэш-производное имя, не содержащее оригинального имени
файла и не идентифицирующее документ само по себе. `mime_type`/`parser`
— общие технические категории (`application/pdf`/`markitdown`), тоже не
идентифицирующие. Единственное реально чувствительное поле source —
`original_filename`: само имя вроде «Консультация уролога.pdf» или
«ВИЧ-анализ.pdf» уже медицинская информация, не только содержимое файла.
Перенос `raw_path`/`mime_type`/`parser` в sidecar не добавил бы защиты,
только развёл бы поля без причины (CLAUDE.md §2) — и, что важнее для
работоспособности пайплайна, `sha256`/`raw_path` нужны СИНХРОННОМУ
дедуп-пути `register_file_for_ingest()` ДО того, как health-сессия вообще
создаётся; переносить их значило бы городить курицу-яйцо там, где его
нет и не должно быть.

### Схема `health`

Три таблицы, отдельная `DeclarativeBase` (`HealthBase`,
`models/health_tables.py`) — вне `migrations/env.py::target_metadata`,
потому что Alembic подключается ролью `helm_app`, у которой на схему
`health` нет вообще никаких прав, включая CREATE:

- `health.knowledge_source_private` — sidecar одной строки на health-
  source: `source_id` (тот же UUID, что конверт, БЕЗ `ForeignKey` на
  `public.knowledge_sources` — см. ниже почему), `knowledge_user_id`,
  `original_filename`, `parse_error` (полный диагностический текст
  парсера, см. «Санитизация ошибок»), `created_at`.
- `health.knowledge_chunks` — зеркало `public.knowledge_chunks` (текст,
  `tsv`, `embedding`), FK на `knowledge_source_private.source_id`
  (внутри своей же схемы — не открывает ничего наружу).
- `health.knowledge_relations` — зеркало `public.knowledge_relations`.
  Добавлена сверх исходно обсуждавшегося списка: `to_id`/`from_id`
  wikilink-связей могут прямо называть тему заметки («аутоиммунный
  гастрит») — то самое "health entities/topics", которому это решение
  запрещает попадать в `public`.

Обе последние — с `FORCE ROW LEVEL SECURITY`, тот же tenant-предикат,
что ADR-030 (`knowledge_user_id = current_setting('app.current_
knowledge_user_id')`) — RLS защищает МЕЖДУ пользователями health-домена,
отдельно от того, что сама схема защищает от `helm_app`.

### Почему `source_id` в sidecar — без FK на конверт

Конверт (`register_file_for_ingest()`/`ingest_text()`, сессия
`helm_app`) и sidecar (та же функция, синхронно следом, но НА ОТДЕЛЬНОМ
соединении ролью `helm_health`) пишутся в ДВУХ РАЗНЫХ транзакциях на
двух разных соединениях. Postgres не видит незакоммиченную строку одной
транзакции при проверке FK из другой — реальная FK через границу схем
здесь была бы гонкой, не гарантией. Ссылочная целостность конверт↔sidecar
проверяется кодом (`knowledge/health_schema.py`), не базой — тот же
класс допущения, что уже есть у `KnowledgeRelation.to_id` (может
указывать на заметку, которой ещё нет).

### Postgres-роль `helm_health`

Схема и роль заведены `compose/init/01-databases.sql` при первом бутстрапе
кластера (`GRANT USAGE`, `REVOKE ALL ON SCHEMA public FROM helm_health`),
но без пароля и без таблиц. `scripts/setup-health-role.sh` (написан,
идемпотентен, ещё не прогнан на живом сервере) достраивает: генерирует
пароль ролью `openssl rand -hex 32` целиком на сервере (тот же приём,
что `restic_password`/`setup_backup.sh` — значение никогда не проходит
через агента, CLAUDE.md §5.4), создаёт три таблицы ОТ ИМЕНИ `helm_health`
(`SET ROLE` перед `CREATE TABLE` — в Postgres владелец объекта обходит
любой `REVOKE` НА этот объект, поэтому «helm_app не видит health»
работает, только если `helm_app` никогда не становится владельцем этих
таблиц), включает RLS, и заканчивается verification-блоком: `helm_health`
не суперпользователь и не BYPASSRLS, `helm_app` не имеет `USAGE` на схему
`health` и не имеет `SELECT` ни на одну из трёх таблиц.

### Деградация (fail-open)

`settings.health_database_url` пусто по умолчанию. Пока `setup-health-
role.sh` не прогнан на конкретном сервере, `health_schema_configured()`
возвращает `False` везде, и весь код в `ingest.py`/`worker.py`/
`probe.py`/`documents.py` откатывается на ТО ЖЕ поведение, что было до
этого ADR — health в `public`, отфильтрован в `probe()` по домену, как
раньше. Включение — не редеплой кода, а появление заполненного секрета
на диске плюс `docker compose restart helm-core helm-knowledge-worker`
(секрет читается один раз при старте процесса, см. `config.py::
_resolve_file_env_vars`). Docker Compose (`compose/docker-compose.yml`)
уже объявляет `health_database_url` как required secret у обоих
контейнеров — порядок деплоя (пустой плейсхолдер-файл до раскатки
compose, реальный пароль — после прогона скрипта) описан в шапке самого
`setup-health-role.sh`.

### Санитизация ошибок

Провал парсера health-документа может процитировать содержимое в тексте
исключения (`"страница 3: обнаружен B12"`). `worker.py::process_job()`
записывает такой текст ТОЛЬКО в `health.knowledge_source_private.
parse_error`; `public.knowledge_ingest_jobs.error` получает исключительно
код `HEALTH_PARSE_FAILED`.

### Health участвует в общем поиске (решение владельца 01.09.2026)

Первая версия этого раздела (написана при разборе P12, тем же днём)
сохраняла старое поведение §14.15: health исключён из общего вопроса
без явного домена, доступен только через `probe(domain="health")`.
В тот же день, на живом использовании (владелец спросил про уровень
холестерина и список врачей обычным вопросом без домена, получил
эскалацию к платной модели вместо ответа из уже загруженных анализов),
владелец это решение отменил прямо: «Все домены должны относиться к
бесплатному второму мозгу, иначе в нём нет смысла. Health не
исключение». Это не смягчение прежней позиции и не оговорка — второе,
более позднее решение того же владельца заменяет первое.

`probe()` теперь ищет health наравне со всеми доменами и на общий
вопрос (`domain=None`), и на явный `domain="health"` — при настроенной
health-схеме оба пути объединяют находки из `public` (прочие домены) и
`health.knowledge_chunks` (`_health_lexical_search`/`_health_vector_
search`, `probe.py`). Единственное оставшееся исключение из общего
поиска — `simpas/zapiski`: это защита приватности КЛИЕНТА (стороннего
человека, не самого владельца), другой по природе вопрос, который это
решение не затрагивает. `documents.py::find_sources()`/`read_original()`
(панель владельца, за passkey-гейтом, `api/panel.py`) читают реальное
имя файла из sidecar — то же законное same-user disclosure, что уже
применялось к уведомлению воркера (`_notify_owner_of_result()`), не
раскрытие постороннему `helm_app`.

## Проверено

Полный набор тестов (`tests/test_knowledge_health_isolation.py`,
584/584 зелёных вместе с остальным набором) — но по honest-scope: роль
там одна и та же (`helm_rls`) для `public` и `health`, потому что вторая
настоящая Postgres-роль требует ручного `CREATEROLE`-шага, не
автоматизируется тестовым прогоном (`tests/README.md`). Что тесты
реально проверяют: RLS-тенантность (`helm_rls` не BYPASSRLS — политики
применяются по-настоящему), и корректность маршрутизации приложения —
`original_filename`/чанки/relations физически лежат в правильной
таблице, `public` получает `None`/пусто, `probe()` находит health и на
явный `domain="health"`, и на общий вопрос без домена.

Acceptance-критерии владельца (сформулированы при разборе P12):

1. health-документ проходит через существующую общую очередь — ✅, тот
   же `KnowledgeIngestJob`, без изменений в его схеме.
2. reboot/retry продолжает работать — ✅, очередь не менялась.
3. `helm_app` SELECT по `public` не раскрывает filename/title/path/text
   health-source — ✅ на уровне данных (`original_filename` — `None`),
   Postgres-REVOKE самой роли — не проверено тестами (см. выше),
   проверяется verification-блоком `setup-health-role.sh` при живом
   прогоне.
4. попытка `helm_app` читать health private tables → permission denied —
   не проверено тестами по той же причине; проверяется тем же
   verification-блоком.
5. **general RAG не находит health chunk — ОТМЕНЕНО владельцем 01.09.2026
   тем же днём**, см. раздел выше: general RAG теперь ДОЛЖЕН находить
   health chunk наравне с любым другим доменом (`test_probe_general_
   query_finds_health_chunk_after_move_to_sidecar`). Пункт оставлен
   здесь зачёркнутым, а не удалён — чтобы следующий читатель видел, что
   это было реальное решение, а не пропуск, и что оно отменено прямо, а
   не забыто.
6. health profile находит его — ✅ (`test_probe_health_domain_finds_
   chunk_only_in_health_schema`).
7. public logs/task_events не содержат sensitive filename/content — ✅
   (`test_process_job_health_domain_sanitizes_error_keeps_diagnostic_
   private`).
8. backup/restore сохраняет связь `public.source_id` ↔ health private
   metadata — не проверено отдельно: `source_id` совпадает по значению
   (не по FK, см. выше), обе схемы живут в одной базе `helm`, значит
   в одном and том же `pg_dump`/restic-снапшоте — та же гарантия
   консистентности, что и у любых двух таблиц одной базы; отдельный
   restore-тест для health не заводился (нет отдельного backup-контура,
   см. `docs/handoff/BACKUP_RESTORE.md`).

## Не в объёме этого ADR

- Живой прогон `scripts/setup-health-role.sh` на боевом сервере и живая
  проверка acceptance #3/#4/#8 против настоящей второй роли — следующий
  шаг, не архитектурное решение.
- Перенос уже существующих 4 живых health-документов (их
  `original_filename` сегодня всё ещё в `public`, заведены до этого
  ADR) — отдельная одноразовая data-миграция.
- Явный синтаксис входа в health-scope из чата (`health: <вопрос>`) —
  предыдущая версия этого документа называла это открытым пробелом;
  решение 01.09.2026 делает его ненужным: раз общий вопрос без домена
  теперь и так находит health, обходить исключение явным синтаксисом
  больше незачем.
- Шифрование на уровне колонки, аудит-лог обращений к health-контенту —
  не запрашивались, не добавлены (CLAUDE.md §2).
