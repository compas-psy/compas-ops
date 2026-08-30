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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ingest import DEFAULT_VAULT_ROOT, ingest_text
from .tenancy import bind_knowledge_user
from ..models import KnowledgeDomain, KnowledgeMemory, KnowledgeMemoryStatus, KnowledgeSource, KnowledgeUser
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


def _confirmation_text(memory: KnowledgeMemory) -> str:
    if memory.kind == "bookmark":
        return f"Запомнил ссылку: {memory.canonical_text}"
    return f"Запомнил: {memory.canonical_text}"


def try_remember(session: Session, *, channel: str, text: str,
                 origin_message_id: str | None = None,
                 knowledge_user_id: uuid.UUID | None = None,
                 vault_root: str = DEFAULT_VAULT_ROOT) -> RememberOutcome:
    """Единая точка входа — вызывается ДО обычного register/probe/chief
    пути, тем же принципом, что `chat_intake.resolve_pending_domain()`
    для вложений: `not_command` значит "это сообщение не про Remember",
    вызывающая сторона продолжает обычный путь как раньше.
    """
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
        origin_message_id=origin_message_id, origin_kind="text",
        # Graphify не реализован (P8.5.6) — тот же "not_applicable", что
        # уже используется у KnowledgeBatchItem.graph_status.
        graph_status="not_applicable",
        tsv=func.to_tsvector("russian", payload),
    )
    session.add(memory)
    session.flush()
    _write_markdown_mirror(memory, vault_root=vault_root)

    return RememberOutcome(status="stored", memory=memory, text=_confirmation_text(memory))
