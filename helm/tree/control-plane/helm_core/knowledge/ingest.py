"""Ingest в HELM Knowledge (ТЗ §14.5) — два пути.

`ingest_text()` — минимальный путь для готового текста, без файла на
диске: сохранить текст с provenance-метаданными и разбить на чанки для
лексического поиска (§14.9). `raw_path`/`source_path` здесь — ожидаемое
расположение, не файл, реально записанный на диск.

`register_file_for_ingest()` — реальный путь для файла, УЖЕ лежащего на
диске (P8.5.2): синхронная "ack" часть pipeline'а — SHA256, создание
`knowledge_sources` + `knowledge_ingest_jobs` (status=PENDING), без
самого парсинга. Парсинг — асинхронный, в отдельном процессе
(`worker.py::process_job`), чтобы тяжёлый Docling-разбор не держал
открытым запрос от Telegram/MAX (§14.5.1: "must not hold the request
open"). Доставка файла ОТ Telegram/MAX В `/opt/helm-knowledge/raw/` —
spool, atomic move — отдельная, ещё не реализованная задача (P8.5.7);
эта функция принимает уже готовый путь, откуда бы он ни взялся.

Общее для обоих путей: дедуп по SHA256 (§14.5 — «повторный файл с тем
же SHA256 не обрабатывается заново, связывается с существующим
source») — единственное правило полного pipeline, не зависящее от
парсеров вообще.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import KnowledgeIngestJob, KnowledgeIngestStatus, KnowledgeSource, KnowledgeStatus
from .atomizer import atomize_and_store
from .chunking import store_chunks
from .health_schema import health_schema_configured, is_health_domain, write_original_filename
from .quotas import check_and_record_ingest, check_queue_depth, record_entry_formed
from .relations import note_id_for, store_relations
from .semantic_jobs import enqueue_semantic
from .temporal import content_date
from .tenancy import bind_knowledge_user
from .vault import frontmatter, scope_root, write_file

#: Корень Vault (§14.2). Параметр, а не только константа: тесты обязаны
#: указывать свой временный каталог — писать в /opt/helm-knowledge при
#: запуске pytest на произвольной машине было бы и неверно, и опасно.
DEFAULT_VAULT_ROOT = "/opt/helm-knowledge"

def _public_original_filename(*, domain: str, original_filename: str | None,
                              source_id: uuid.UUID, knowledge_user_id: uuid.UUID) -> str | None:
    """ADR-005/P12: для `health`, если health-схема настроена, реальное
    имя файла уходит в `health.knowledge_source_private` (единственное
    чувствительное поле, см. докстринг `HealthKnowledgeSourcePrivate`),
    а в `public.knowledge_sources` остаётся `None`. Если health-схема ещё
    не настроена (`scripts/setup-health-role.sh` не прогнан на этом
    сервере) — деградация на прежнее поведение: имя остаётся в `public`
    как у любого другого домена, не падаем и не теряем данные."""
    if not is_health_domain(domain) or not health_schema_configured():
        return original_filename
    write_original_filename(source_id=source_id, knowledge_user_id=knowledge_user_id,
                            original_filename=original_filename)
    return None


def ingest_text(session: Session, *, domain: str, text: str,
                original_filename: str | None = None,
                sensitivity: str = "internal", trust: str = "extracted",
                vault_root: str | None = None,
                knowledge_user_id: uuid.UUID | None = None,
                count_entry: bool = True) -> KnowledgeSource:
    """Сохранить текст как source + лексически проиндексированные чанки.

    Повторный вызов с тем же текстом ОТ ТОГО ЖЕ knowledge_user_id
    возвращает уже существующий source, не создаёт дубль (SHA256-дедуп,
    §14.5) — дедуп per-tenant (v3.8 §14.4: идентичные байты у разных
    пользователей НЕ схлопываются в одну запись).

    `knowledge_user_id=None` — существующие call sites (P8.6.2 Dedicated
    Knowledge Bot ещё не существует): разрешается в SYSTEM_OWNER.
    """
    # `None` + резолв здесь, не литеральный default в сигнатуре — тот же
    # приём, что уже применён в memory.py/offboarding.py: default-значение
    # именованного параметра вычисляется ОДИН РАЗ при определении функции,
    # подмена `ingest.DEFAULT_VAULT_ROOT` тестовой фикстурой
    # (`_never_touch_the_real_vault`) на уже связанный default не подействует.
    # Раньше это было безобидно (ingest_text() ничего не писал на диск), но
    # ADR-019 (atomize_and_store() ниже) пишет .md-файлы атомов — без этой
    # правки тесты без явного vault_root писали бы в настоящий Vault.
    vault_root = vault_root or DEFAULT_VAULT_ROOT
    knowledge_user_id = bind_knowledge_user(session, knowledge_user_id)

    sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = session.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.knowledge_user_id == knowledge_user_id,
            KnowledgeSource.sha256 == sha256,
        )
    )
    if existing is not None:
        return existing

    root = scope_root(vault_root, domain=domain, knowledge_user_id=knowledge_user_id)
    source = KnowledgeSource(
        knowledge_user_id=knowledge_user_id, domain=domain, sha256=sha256,
        raw_path=f"{root}/raw/{domain}/{sha256}.txt",
        source_path=f"{root}/sources/{sha256}.md",
        original_filename=original_filename, mime_type="text/plain", parser="manual",
        sensitivity=sensitivity, trust=trust, status=KnowledgeStatus.ACTIVE,
    )
    session.add(source)
    session.flush()  # source.id нужен ДО вызова ниже — sidecar ссылается на него по значению, не по FK.

    # ADR-005/P12: та же маршрутизация, что у register_file_for_ingest().
    source.original_filename = _public_original_filename(
        domain=domain, original_filename=original_filename,
        source_id=source.id, knowledge_user_id=knowledge_user_id)

    # `count_entry=False` — источник производный, а не отдельное
    # действие владельца: «Запомни» уже посчитано как запись памяти, и
    # считать то же сообщение дважды значило бы вдвое быстрее упирать
    # владельца в его же квоту.
    if count_entry:
        record_entry_formed(session, knowledge_user_id=knowledge_user_id, sources=1)

    # ИСХОДНИК НА ДИСК. До 06.09.2026 `ingest_text()` записывал в базу
    # ПУТИ к файлам, которых не создавал: `raw_path` и `source_path`
    # указывали в пустоту. Последствий было два, и оба видны в живой
    # системе. Первое: `source_text()` возвращает None, семантический
    # разбор такого источника падает с NoText — то есть текст в граф не
    # попадал никогда (найдено прогоном 376). Второе: §14.15 «выдача
    # оригинала» на такой источник честно отвечала «исходного файла
    # нет».
    #
    # Пишется и сырой текст, и нормализованная заметка с фронтматтером
    # — ровно то же, что делает воркер после разбора файла (worker.py),
    # тем же `frontmatter()`. Сырой файл — байт-в-байт исходный текст:
    # по совпадению его sha256 с записанным §14.15 решает, отдавать ли
    # оригинал.
    # Дата документа — из его же текста, до всякой семантики: по ней
    # отвечается «в последний раз» и ею подписывается ответ.
    source.content_date = content_date(text)
    write_file(source.raw_path, text.encode("utf-8"))
    write_file(source.source_path, (frontmatter(source) + text).encode("utf-8"))

    # Семантика ставится заданием — так же, как после разбора файла
    # (worker.py). Без этого текстовый путь оставался вторым сортом:
    # чанки есть, узлов и связей нет, в графе содержания не существует.
    enqueue_semantic(session, source_id=source.id, knowledge_user_id=knowledge_user_id,
                     source_sha256=sha256)

    # P8.5.6 слой 1 (E13, решение владельца 31.08.2026): [[wikilink]] +
    # явный YAML relations: — детерминированно, до любого Graphify.
    # ADR-005/P12: note_id_for() получает исходный original_filename (не
    # `source.original_filename`, уже перезаписанный выше на None для
    # health) — from_id для wikilink-резолва нужен независимо от того,
    # куда физически уехала сама запись relation.
    store_relations(session, domain=domain, knowledge_user_id=knowledge_user_id,
                    from_id=note_id_for(original_filename=original_filename, source_id=source.id),
                    source_id=source.id, text=text)

    # ADR-019: L2 semantic atomizer — поверх уже сделанного store_relations()
    # выше, аддитивно (fail-open: недоступность атомизатора не мешает
    # созданию source/chunks/слоя-1-relations, см. atomizer.py).
    atomize_and_store(session, domain=domain, knowledge_user_id=knowledge_user_id,
                      source_id=source.id, source_sha256=sha256, text=text,
                      vault_root=root)

    store_chunks(session, source_id=source.id, knowledge_user_id=knowledge_user_id,
                 domain=domain, text=text)
    # Флаш здесь, а не когда придётся: привязка тенанта транзакционна
    # (`set_config(..., true)`), и отложенные вставки чанков ушли бы в
    # базу уже под ДРУГИМ пользователем, если следующий вызов успел
    # перепривязать сессию. RLS такую вставку отвергает — и правильно
    # делает; но ловить это на автофлаше посреди чужого запроса нельзя.
    session.flush()
    return source


@dataclass
class RegisterFileResult:
    source: KnowledgeSource
    #: None означает «уже проиндексирован раньше» (SHA256-дедуп) — новой
    #: работы для воркера нет, job не создаётся.
    job: KnowledgeIngestJob | None
    created: bool


def register_file_for_ingest(session: Session, *, domain: str, raw_path: Path,
                             original_filename: str | None = None,
                             mime_type: str | None = None,
                             sensitivity: str = "internal", trust: str = "extracted",
                             channel: str | None = None, recipient: str | None = None,
                             vault_root: str | None = None,
                             knowledge_user_id: uuid.UUID | None = None) -> RegisterFileResult:
    """Зарегистрировать файл, уже лежащий на диске, для асинхронного парсинга.

    Быстрая синхронная часть pipeline'а (§14.5.1: "immediate
    acknowledgement") — читает файл только чтобы посчитать SHA256, сам
    парсинг не запускает. Дедуп: повторный файл с тем же содержимым ОТ
    ТОГО ЖЕ knowledge_user_id возвращает существующий source без нового
    ingest job — per-tenant (v3.8 §14.4), не глобальный SHA256.

    `knowledge_user_id=None` — существующие call sites (P8.6.2 Dedicated
    Knowledge Bot ещё не существует): разрешается в SYSTEM_OWNER.
    """
    # См. ingest_text() выше — тот же приём и та же причина.
    vault_root = vault_root or DEFAULT_VAULT_ROOT
    knowledge_user_id = bind_knowledge_user(session, knowledge_user_id)

    data = raw_path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    existing = session.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.knowledge_user_id == knowledge_user_id,
            KnowledgeSource.sha256 == sha256,
        )
    )
    if existing is not None:
        return RegisterFileResult(source=existing, job=None, created=False)

    # §14.4 "oversized user upload rejected before resource exhaustion" —
    # ДО записи source, не постфактум; дубликаты (проверка выше) не
    # тарифицируются повторно. check_queue_depth() здесь же покрывает и
    # ZIP-члены (каждый идёт через эту же функцию, batch_intake.py::
    # _process_item()) — при переполнении очереди дальнейшие члены
    # батча просто получают FAILED/retryable, не рушат уже принятые.
    check_and_record_ingest(session, knowledge_user_id=knowledge_user_id, size_bytes=len(data))
    check_queue_depth(session, knowledge_user_id=knowledge_user_id)

    root = scope_root(vault_root, domain=domain, knowledge_user_id=knowledge_user_id)
    source = KnowledgeSource(
        knowledge_user_id=knowledge_user_id, domain=domain, sha256=sha256, raw_path=str(raw_path),
        source_path=f"{root}/sources/{sha256}.md",
        original_filename=original_filename, mime_type=mime_type, parser=None,
        sensitivity=sensitivity, trust=trust, status=KnowledgeStatus.ACTIVE,
    )
    session.add(source)
    session.flush()  # source.id нужен ДО вызова ниже — sidecar ссылается на него по значению, не по FK.

    # ADR-005/P12: для health реальное имя файла уезжает в health-схему,
    # public.knowledge_sources.original_filename перезаписывается на None.
    source.original_filename = _public_original_filename(
        domain=domain, original_filename=original_filename,
        source_id=source.id, knowledge_user_id=knowledge_user_id)

    record_entry_formed(session, knowledge_user_id=knowledge_user_id, sources=1)

    job = KnowledgeIngestJob(knowledge_user_id=knowledge_user_id, source_id=source.id,
                             channel=channel, recipient=recipient,
                             status=KnowledgeIngestStatus.PENDING)
    session.add(job)
    session.flush()
    return RegisterFileResult(source=source, job=job, created=True)
