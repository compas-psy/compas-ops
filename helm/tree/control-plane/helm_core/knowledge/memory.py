"""Micro-Memory «Запомни» (v3.8 §14.10-14.11, P8.5.12).

НЕ document source: минуя MarkItDown/Docling/chunker, прямой FTS-юнит,
ноль платного AI (детерминированные префиксы/regex, без LLM вообще —
Ollama structured classifier не реализован, тот же П8.5.6 остаток, что
уже задокументирован в V3.8-DELTA.md). Голос (GigaAM) не реализован
вовсе (GigaAM нигде в кодовой базе не подключён) — этот модуль работает
только с готовым текстом; голосовой путь ("voice → GigaAM → transcript
→ тот же Remember-путь") — явный, задокументированный пробел, не эта
функция.

Осознанно упрощено против буквы спеки (см. V3.8-DELTA.md):
- `kind` — только `bookmark` (текст — в основном URL) или `note`
  (всё остальное); `fact`/`identifier`/`preference`/`temporary` из
  спеки не различаются классификатором (сама спека это разрешает:
  "kind optimizes rendering/retrieval; canonical_text+payload_json
  remain flexible" — enum пока не сужен, значения не в счёт).
- `domain` всегда `None` — спека прямо разрешает ("retrieval remains
  global so this never hides memory"), эвристика "high-confidence
  local match" не строится (то же решение, что и для L1 SOURCE domain
  в v3.7 — реестра доменов нет).
- URL canonicalization (снятие tracking-параметров) — не делается,
  `original_url` хранится как есть.
- Reply/forwarded-text как источник payload'а ("Запомни это" в ответ
  на сообщение) — не реализовано: нужна channel-специфичная выборка
  "текста, на который ответили" из webhook-полезной нагрузки, которой
  сегодня ни один из каналов (MAX/Telegram) не передаёт в HELM Core.
  Явный, задокументированный пробел.
- Temporal parsing — только "сегодня"/"завтра" (конец локальных суток).
  Полноценный NLU-парсер дат не строится.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .ingest import DEFAULT_VAULT_ROOT, ingest_text
from .quotas import record_entry_formed
from .tenancy import bind_knowledge_user
from ..models import (KnowledgeChunk, KnowledgeDomain, KnowledgeMemory, KnowledgeMemoryStatus,
                      KnowledgeSource, KnowledgeStatus, KnowledgeUser)
from ..models.base import utcnow

#: §14.10: "default starting point 8,000 chars" — за этим порогом текст
#: становится SOURCE (через уже работающий ingest_text()), не одним
#: memory-объектом.
MICRO_MEMORY_MAX_CHARS = 8000

#: Домен, в который уходит текст, превысивший MICRO_MEMORY_MAX_CHARS —
#: ближайший существующий смысл к "неразобранному личному", реестра
#: доменов нет (V3.7-DELTA.md), новый домен не заводится ради этого.
_OVERFLOW_DOMAIN = KnowledgeDomain.PERSONAL.value

#: Порядок важен: более длинные/специфичные фразы раньше более общих
#: только там, где это меняет результат (здесь — нет пересечений).
_REMEMBER_PREFIX = re.compile(
    r"^\s*(?:"
    r"/remember\b"
    r"|запомни(?:те)?\b"
    r"|сохрани(?:те)?\s+в\s+память\b"
    r"|не\s+забудь(?:те)?\b"
    r")\s*[:,\-—]?\s*",
    re.IGNORECASE,
)

#: Детекция запрещённого секрета — консервативно (ложное срабатывание
#: безопаснее пропуска, §14.10 "Detect and refuse storing"): наличие
#: метки секрета РЯДОМ с текстом достаточно для отказа, без попытки
#: точно извлечь и провалидировать само значение.
_FORBIDDEN_SECRET_LABELS = re.compile(
    r"(?:"
    r"парол[ья]"
    r"|password"
    r"|секретн(?:ый|ого)\s+ключ"
    r"|private\s*key"
    r"|api[\s_-]?key"
    r"|api[\s_-]?token"
    r"|токен\s+досту[пв]а"
    r"|\bcvv2?\b"
    r"|\botp\b"
    r"|одноразов(?:ый|ого)\s+код"
    r"|код\s+подтверждени[яе]"
    r"|recovery\s+code"
    r"|seed\s+phrase"
    r"|мнемоническ(?:ая|ую)\s+фраз[ау]"
    r")",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

_WHITESPACE_RE = re.compile(r"\s+")

FORBIDDEN_SECRET_NOTICE = (
    "Не сохраняю: похоже на пароль/код доступа/секретный ключ. "
    "Используйте менеджер паролей — это не место для секретов."
)


def detect_remember_command(text: str) -> str | None:
    """Вернуть payload БЕЗ команды-префикса, либо None — не Remember-команда.

    Пустой payload (одно только "Запомни" без содержимого) — тоже None:
    вызывающей стороне тогда решать, что ответить ("что запомнить?"),
    это не задача этого модуля."""
    match = _REMEMBER_PREFIX.match(text)
    if match is None:
        return None
    payload = text[match.end():].strip()
    return payload or None


def is_forbidden_secret(text: str) -> bool:
    return _FORBIDDEN_SECRET_LABELS.search(text) is not None


def _normalize_for_dedup(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip().casefold()


def compute_dedup_hash(text: str) -> str:
    return hashlib.sha256(_normalize_for_dedup(text).encode("utf-8")).hexdigest()


def extract_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    return match.group(0) if match else None


def classify_kind(text: str) -> Literal["bookmark", "note"]:
    """§14.10 "Do not create dozens of hardcoded memory schemas" — только
    различие, которое реально меняет рендер (ссылка против остального)."""
    url = extract_url(text)
    if url is None:
        return "note"
    # Текст — по существу голая ссылка (плюс, возможно, короткая метка/
    # контекст владельца) — не абзац, ГДЕ упомянута ссылка среди прочего.
    remainder = text.replace(url, "").strip()
    return "bookmark" if len(remainder) <= len(url) else "note"


#: §14.10 "Explicit temporal language is parsed locally" — только два
#: слова из явного acceptance-примера ("курьер, который приедет
#: сегодня"), не общий парсер дат.
_TODAY_RE = re.compile(r"\bсегодня\b", re.IGNORECASE)
_TOMORROW_RE = re.compile(r"\bзавтра\b", re.IGNORECASE)


def parse_temporal_expiry(text: str, *, timezone_name: str, now: datetime) -> datetime | None:
    """Конец локальных суток "сегодня"/"завтра" — или None, если явного
    временного маркера нет (§14.10 "Do not invent expiry without an
    explicit temporal cue")."""
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")
    local_now = now.astimezone(tz)
    if _TOMORROW_RE.search(text):
        target_date = (local_now + timedelta(days=1)).date()
    elif _TODAY_RE.search(text):
        target_date = local_now.date()
    else:
        return None
    end_of_day_local = datetime.combine(target_date, time(23, 59, 59), tzinfo=tz)
    return end_of_day_local.astimezone(now.tzinfo or ZoneInfo("UTC"))


def _markdown_mirror_path(vault_root: str, knowledge_user_id: uuid.UUID,
                          memory_id: uuid.UUID) -> Path:
    return Path(vault_root) / "users" / str(knowledge_user_id) / "memory" / f"{memory_id}.md"


def _write_markdown_mirror(memory: KnowledgeMemory, *, vault_root: str) -> None:
    """§14.11: детерминированное зеркало для Obsidian/Graphify —
    "canonical lifecycle state = PostgreSQL", это НИКОГДА не единственное
    хранилище статуса, только дополнительное представление."""
    path = _markdown_mirror_path(vault_root, memory.knowledge_user_id, memory.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = "\n".join([
        "---",
        f"id: memory:{memory.id}",
        f"type: {memory.kind}",
        f"domain: {memory.domain or ''}",
        f"status: {memory.status}",
        f"created_at: {memory.created_at.isoformat()}",
        f"expires_at: {memory.expires_at.isoformat() if memory.expires_at else 'null'}",
        "---",
        "",
        memory.canonical_text,
        "",
    ])
    path.write_text(frontmatter, encoding="utf-8")


@dataclass
class RememberOutcome:
    status: Literal["not_command", "empty", "rejected_secret", "stored", "duplicate",
                    "stored_as_source"]
    memory: KnowledgeMemory | None = None
    source: KnowledgeSource | None = None
    text: str | None = None


#: Сколько символов первой строки берётся в заголовок заметки. Имя
#: источника видно владельцу в ответе («Источники: …»), поэтому это
#: первая строка запомненного, а не «memory-<uuid>.md».
_TITLE_MAX_CHARS = 60


def _note_title(payload: str) -> str:
    first_line = payload.strip().splitlines()[0].strip() if payload.strip() else ""
    title = first_line[:_TITLE_MAX_CHARS].rstrip(" :,-—")
    return title or "заметка"


def _confirmation_text(memory: KnowledgeMemory) -> str:
    if memory.kind == "bookmark":
        return f"Запомнил ссылку: {memory.canonical_text}"
    return f"Запомнил: {memory.canonical_text}"


def try_remember(session: Session, *, channel: str, text: str,
                 origin_message_id: str | None = None,
                 knowledge_user_id: uuid.UUID | None = None,
                 vault_root: str | None = None,
                 origin_kind: Literal["text", "voice"] = "text") -> RememberOutcome:
    """Единая точка входа — вызывается ДО обычного register/probe/chief
    пути, тем же принципом, что `chat_intake.resolve_pending_domain()`
    для вложений: `not_command` значит "это сообщение не про Remember",
    вызывающая сторона продолжает обычный путь как раньше.
    """
    vault_root = vault_root or DEFAULT_VAULT_ROOT
    payload = detect_remember_command(text)
    if payload is None:
        return RememberOutcome(status="not_command")

    knowledge_user_id = bind_knowledge_user(session, knowledge_user_id)

    if is_forbidden_secret(payload):
        # §14.10: "forbidden secret text is not written to normal DB/
        # log/Markdown mirror" — возврат ДО любой записи куда бы то ни
        # было, текст secret'а не попадает даже в это исключение/лог.
        return RememberOutcome(status="rejected_secret", text=FORBIDDEN_SECRET_NOTICE)

    if len(payload) > MICRO_MEMORY_MAX_CHARS:
        # §14.10: "preserve it as a text SOURCE instead of forcing it
        # into one memory item" — уже работающий путь ingest_text(), не
        # новый код; per-tenant дедуп там уже есть (v3.8 Фаза 1).
        source = ingest_text(session, domain=_OVERFLOW_DOMAIN, text=payload,
                             knowledge_user_id=knowledge_user_id)
        return RememberOutcome(
            status="stored_as_source", source=source,
            text="Текст длинный — сохранил как документ, не как быструю заметку.",
        )

    dedup_hash = compute_dedup_hash(payload)
    existing = session.scalar(
        select(KnowledgeMemory).where(
            KnowledgeMemory.knowledge_user_id == knowledge_user_id,
            KnowledgeMemory.dedup_hash == dedup_hash,
            KnowledgeMemory.status == KnowledgeMemoryStatus.ACTIVE,
        )
    )
    if existing is not None:
        # §14.10 "Exact repeat → no second active item" — не ошибка,
        # просто ссылаемся на уже существующую запись.
        return RememberOutcome(status="duplicate", memory=existing,
                               text=_confirmation_text(existing))

    kind = classify_kind(payload)
    timezone_name = session.scalar(
        select(KnowledgeUser.timezone).where(KnowledgeUser.id == knowledge_user_id)
    ) or "Europe/Moscow"
    expires_at = parse_temporal_expiry(payload, timezone_name=timezone_name, now=utcnow())

    memory = KnowledgeMemory(
        knowledge_user_id=knowledge_user_id, kind=kind, canonical_text=payload,
        payload_json={"url": extract_url(payload)} if kind == "bookmark" else None,
        dedup_hash=dedup_hash, expires_at=expires_at,
        status=KnowledgeMemoryStatus.ACTIVE, origin_channel=channel,
        origin_message_id=origin_message_id, origin_kind=origin_kind,
        # Graphify не реализован (P8.5.6) — тот же "not_applicable", что
        # уже используется у KnowledgeBatchItem.graph_status.
        graph_status="not_applicable",
        tsv=func.to_tsvector("russian", payload),
    )
    session.add(memory)
    session.flush()
    record_entry_formed(session, knowledge_user_id=knowledge_user_id, memories=1)
    _write_markdown_mirror(memory, vault_root=vault_root)

    # ЗАПОМНЕННОЕ ИДЁТ ОБЩИМ ЖИЗНЕННЫМ ЦИКЛОМ, а не только в свою
    # таблицу. Распоряжение владельца 06.09.2026: «Не оставляй
    # отдельную „быструю память", которая сохраняет сплошную строку и
    # не участвует в общей обработке. Быстрый поиск допустим как
    # оптимизация поверх тех же знаний».
    #
    # Что это чинит, по замеру разведки (прогон 385) на живом сбое
    # «Запомни ссылки на мои каналы» → «Дай ссылку на мой канал B17»:
    # запись в `knowledge_memories` была, но искалась только своим
    # лексическим поиском, а он на ней не сработал — ранг 0.000390 при
    # пороге 0.003 (`ts_rank` делит на длину, а список из девяти
    # площадок длинный) и лемма `b17.ru` из адреса не совпадает с
    # леммой `b17` из вопроса. Чанков и узлов графа у этого текста не
    # было вовсе: 0 чанков со словом b17 на весь корпус.
    #
    # Теперь тот же текст становится обычным источником: чанки,
    # эмбеддинги, задание на семантику, wikilink-связи. Векторный поиск
    # и синтез работают по нему, как по любому загруженному документу.
    # Строка памяти остаётся — она нужна командам §14.16 («Забудь
    # это») и даёт дословный ответ, — но перестаёт быть единственным
    # местом, где это знание существует.
    # ИСКЛЮЧЕНИЕ — записи со сроком. «Напомни до пятницы» это текущий
    # контекст (§14.10), а не знание: после срока такой памяти не должно
    # быть ни в ответах, ни в поиске. У источника поля срока нет, и
    # исключать его из выдачи было бы нечем — поэтому срочная запись
    # остаётся только быстрой записью, как и была.
    source = None
    if expires_at is None:
        source = ingest_text(session, domain=_OVERFLOW_DOMAIN, text=payload,
                             original_filename=_note_title(payload),
                             knowledge_user_id=knowledge_user_id, vault_root=vault_root,
                             count_entry=False)
        memory.source_id = source.id
    # Флаш под ТЕКУЩЕЙ привязкой тенанта: она транзакционна, и
    # отложенный UPDATE ушёл бы в базу уже под другим пользователем —
    # RLS такую строку не увидит, и SQLAlchemy сообщит «0 rows matched».
    session.flush()

    return RememberOutcome(status="stored", memory=memory, source=source,
                           text=_confirmation_text(memory))


def backfill_memory_sources(session: Session, *, limit: int | None = None,
                            vault_root: str | None = None) -> tuple[int, int]:
    """Догнать записи «Запомни», сделанные ДО общего жизненного цикла.

    Возвращает «сколько догнали, сколько осталось». Идемпотентно: берутся
    только записи с пустым `source_id`, и повтор прогона на догнанной
    памяти не делает ничего.

    Нужно ровно потому, что владелец сохранил ссылки на свои каналы
    раньше этой правки: без догоняющего прохода его собственная запись
    так и осталась бы вне поиска, а «починено» относилось бы только к
    будущим сообщениям.

    Коммитит каждую запись отдельно, как `backfill.run_backfill()`:
    прогон, оборванный на середине, оставляет догнанное догнанным.

    Идёт по ОДНОМУ тенанту — тому, к которому привязана сессия: RLS
    чужих записей и не покажет, а обходить её ради догоняющего прохода
    нельзя. Для второго пользователя проход запускается его сессией.
    """
    done = 0
    while limit is None or done < limit:
        # Привязка тенанта транзакционна и пропадает после каждого
        # коммита ниже — восстанавливается на каждом витке, иначе
        # следующий запрос ушёл бы без неё и RLS вернула бы пусто, а
        # прогон выглядел бы законченным.
        tenant = bind_knowledge_user(session, None)
        memory = session.scalars(
            select(KnowledgeMemory)
            .where(KnowledgeMemory.knowledge_user_id == tenant,
                   KnowledgeMemory.source_id.is_(None),
                   KnowledgeMemory.status == KnowledgeMemoryStatus.ACTIVE)
            .order_by(KnowledgeMemory.created_at)
            .limit(1)).first()
        if memory is None:
            break
        source = ingest_text(session, domain=_OVERFLOW_DOMAIN,
                             text=memory.canonical_text,
                             original_filename=_note_title(memory.canonical_text),
                             knowledge_user_id=tenant, vault_root=vault_root,
                             count_entry=False)
        memory.source_id = source.id
        session.flush()
        session.commit()
        done += 1
    tenant = bind_knowledge_user(session, None)
    remaining = session.scalar(
        select(func.count()).select_from(KnowledgeMemory)
        .where(KnowledgeMemory.knowledge_user_id == tenant,
               KnowledgeMemory.source_id.is_(None),
               KnowledgeMemory.status == KnowledgeMemoryStatus.ACTIVE)) or 0
    return done, remaining


def archive_memory_source(session: Session, memory: KnowledgeMemory) -> None:
    """«Забудь это» — убрать связанный источник из обычных ответов.

    Без этого забытое продолжало бы отвечать: строка памяти
    исключается своим статусом, а чанки того же текста живут отдельно и
    про запрет ничего не знают. Проверено тестом
    `test_disabled_memory_never_returns_even_historically`, который на
    первой же версии общего цикла и покраснел.

    ARCHIVED, а не удаление: «Забудь» обратимо («Верни в память»), и
    восстанавливать удалённые чанки было бы неоткуда.
    """
    if memory.source_id is None:
        return
    source = session.get(KnowledgeSource, memory.source_id)
    if source is not None:
        source.status = KnowledgeStatus.ARCHIVED


def restore_memory_source(session: Session, memory: KnowledgeMemory) -> None:
    """«Верни в память» — вернуть источник в обычные ответы."""
    if memory.source_id is None:
        return
    source = session.get(KnowledgeSource, memory.source_id)
    if source is not None:
        source.status = KnowledgeStatus.ACTIVE


def purge_memory_source(session: Session, memory: KnowledgeMemory) -> None:
    """«Удали навсегда» — убрать содержание из поиска и с диска.

    Удаляются чанки (поисковый слой) и файлы Vault; строка источника
    остаётся ARCHIVED-надгробием, потому что на неё ссылаются прогоны
    семантики и упоминания.

    ЧЕГО ЭТО НЕ ДЕЛАЕТ: узлы семантического графа, если разбор успел
    пройти, содержат тот же текст в `statement_text` и здесь не
    трогаются. Обычные ответы их не показывают (структурный путь ходит
    в граф только за врачами), но «навсегда» это делает неполным.
    Названо вслух, а не умолчано; отдельная задача.
    """
    if memory.source_id is None:
        return
    source = session.get(KnowledgeSource, memory.source_id)
    if source is None:
        return
    session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id))
    for path in (source.raw_path, source.source_path):
        if path:
            Path(path).unlink(missing_ok=True)
    source.status = KnowledgeStatus.ARCHIVED


def replace_memory_source(session: Session, memory: KnowledgeMemory, *,
                          vault_root: str | None = None) -> KnowledgeSource:
    """«Исправь …» — прежний источник в архив, новый текст обычным путём.

    Не правка источника на месте: sha256 источника — его удостоверение
    (§14.1 RAW immutable, по нему же §14.15 решает, отдавать ли
    оригинал), и переписать текст, оставив хэш, значило бы сломать
    проверяемость. Прежняя версия остаётся ARCHIVED — «различение
    актуальных и прежних сведений», а не потеря истории.
    """
    archive_memory_source(session, memory)
    source = ingest_text(session, domain=_OVERFLOW_DOMAIN, text=memory.canonical_text,
                         original_filename=_note_title(memory.canonical_text),
                         knowledge_user_id=memory.knowledge_user_id, vault_root=vault_root,
                         count_entry=False)
    memory.source_id = source.id
    return source
