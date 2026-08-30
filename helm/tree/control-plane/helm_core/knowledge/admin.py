"""v3.8 §14.16 — управление памятью словами, без платного классификатора.

Замыкает жизненный цикл Micro-Memory. До этого модуля состояние
«забыто» существовало в схеме, но достичь его можно было только правкой
базы руками: «Запомни» умел создавать, и больше ничего.

Команды (спека §14.16):

    Забудь <что>          → DISABLED, обратимо
    Верни в память <что>  → обратно в ACTIVE
    Удали навсегда <что>  → физическое удаление, необратимо
    Исправь <что>: <как>  → правка текста без потери самой записи

Как находится «что». Спека говорит только «target resolution is scoped
to authenticated user only», способ не задаёт. Здесь — тот же
полнотекстовый поиск, что и у recall, по своему тенанту:

- ровно одно совпадение → действуем и показываем текст, чтобы человек
  видел, ЧТО именно затронуто;
- несколько → перечисляем и просим уточнить. Уточняющего диалога с
  состоянием нет (он потребовал бы pending-состояния канала, как у
  вложений) — человек просто повторяет команду точнее. Для действий,
  половина которых необратима, «переспросить» безопаснее, чем угадать;
- ничего → так и говорим.

Ответ на чужое сообщение («Забудь это» реплаем) как способ указать цель
не реализован: ни один канал сегодня не передаёт в HELM текст, на
который отвечают. Тот же задокументированный пробел, что у «Запомни
это».

Ноль платного AI: разбор — регулярки, поиск — PostgreSQL, решение —
сравнение чисел.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ingest import DEFAULT_VAULT_ROOT
from .memory import _markdown_mirror_path, _write_markdown_mirror, compute_dedup_hash
from .probe import MIN_RANK_SCORE
from .recall import MAX_MEMORY_HITS, build_or_tsquery
from .tenancy import bind_knowledge_user
from ..models import KnowledgeMemory, KnowledgeMemoryStatus
from ..models.base import utcnow

from sqlalchemy import func

#: Порядок важен: «удали навсегда» обязано проверяться раньше «удали»,
#: иначе необратимое действие попало бы в обратимую ветку по совпадению
#: более короткого префикса.
_PURGE_RE = re.compile(
    r"^\s*(?:удали|сотри)\s+(?:это\s+)?навсегда\b\s*[:,\-—]?\s*", re.IGNORECASE)
_RESTORE_RE = re.compile(
    r"^\s*(?:верни(?:те)?\s+в\s+память|восстанови(?:те)?)\b\s*[:,\-—]?\s*", re.IGNORECASE)
#: `^\s*забудь` с якорем на начало — «Не забудь купить молоко» начинается
#: с «не» и сюда не попадает, оставаясь командой «Запомни» (memory.py).
_FORGET_RE = re.compile(
    r"^\s*(?:забудь(?:те)?|не\s+используй)\b\s*(?:это|этот\s+документ)?\s*[:,\-—]?\s*",
    re.IGNORECASE)
_FIX_RE = re.compile(r"^\s*исправь(?:те)?\b\s*[:,\-—]?\s*", re.IGNORECASE)

#: «Исправь <что>: <как>» — двоеточие отделяет цель от нового текста.
_FIX_SPLIT_RE = re.compile(r"\s+(?:на|→)\s+|\s*:\s*")

AdminKind = Literal["forget", "restore", "purge", "fix"]


@dataclass(frozen=True)
class AdminCommand:
    kind: AdminKind
    #: Чем искать цель.
    target: str
    #: Новый текст — только для `fix`.
    replacement: str | None = None


def detect_admin_command(text: str) -> AdminCommand | None:
    """Разобрать команду управления памятью или вернуть None."""
    for kind, pattern in (("purge", _PURGE_RE), ("restore", _RESTORE_RE),
                          ("fix", _FIX_RE), ("forget", _FORGET_RE)):
        match = pattern.match(text)
        if match is None:
            continue
        payload = text[match.end():].strip()
        if not payload:
            return None
        if kind == "fix":
            parts = _FIX_SPLIT_RE.split(payload, maxsplit=1)
            if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                return None
            return AdminCommand(kind="fix", target=parts[0].strip(),
                                replacement=parts[1].strip())
        return AdminCommand(kind=kind, target=payload)  # type: ignore[arg-type]
    return None


@dataclass
class AdminOutcome:
    status: Literal["not_command", "not_found", "ambiguous", "forgotten", "restored",
                    "purged", "corrected", "duplicate"]
    text: str | None = None
    memory: KnowledgeMemory | None = None


def _find_targets(session: Session, *, query: str, knowledge_user_id: uuid.UUID,
                  statuses: tuple[str, ...]) -> list[KnowledgeMemory]:
    tsquery = build_or_tsquery(query)
    rank = func.ts_rank(KnowledgeMemory.tsv, tsquery, 2).label("rank")
    rows = session.execute(
        select(KnowledgeMemory, rank)
        .where(KnowledgeMemory.knowledge_user_id == knowledge_user_id)
        .where(KnowledgeMemory.status.in_(statuses))
        .where(KnowledgeMemory.tsv.op("@@")(tsquery))
        .order_by(rank.desc(), KnowledgeMemory.created_at.desc())
        .limit(MAX_MEMORY_HITS)
    ).all()
    return [memory for memory, score in rows if float(score) >= MIN_RANK_SCORE]


def _ambiguous_text(candidates: list[KnowledgeMemory]) -> str:
    lines = ["Нашлось несколько — уточните, какую именно:"]
    lines.extend(f"{i}. {m.canonical_text}" for i, m in enumerate(candidates, 1))
    return "\n".join(lines)


def try_admin_command(session: Session, *, text: str,
                      knowledge_user_id: uuid.UUID | None = None,
                      vault_root: str | None = None) -> AdminOutcome:
    """Единая точка входа, тем же принципом, что `try_remember()`:
    `not_command` значит «это сообщение не про управление памятью»,
    вызывающая сторона продолжает обычный путь."""
    vault_root = vault_root or DEFAULT_VAULT_ROOT
    command = detect_admin_command(text)
    if command is None:
        return AdminOutcome(status="not_command")

    knowledge_user_id = bind_knowledge_user(session, knowledge_user_id)

    # «Верни в память» ищет среди забытых, всё остальное — среди живых.
    statuses = ((KnowledgeMemoryStatus.DISABLED,) if command.kind == "restore"
                else (KnowledgeMemoryStatus.ACTIVE, KnowledgeMemoryStatus.EXPIRED))
    candidates = _find_targets(session, query=command.target,
                               knowledge_user_id=knowledge_user_id, statuses=statuses)
    if not candidates:
        missing = ("Среди забытого ничего похожего не нашёл."
                   if command.kind == "restore"
                   else "В памяти ничего похожего не нашёл.")
        return AdminOutcome(status="not_found", text=missing)
    if len(candidates) > 1:
        return AdminOutcome(status="ambiguous", text=_ambiguous_text(candidates))

    memory = candidates[0]
    mirror = _markdown_mirror_path(vault_root, knowledge_user_id, memory.id)

    if command.kind == "forget":
        memory.status = KnowledgeMemoryStatus.DISABLED
        memory.updated_at = utcnow()
        # §14.11: зеркало исключается, когда запись DISABLED/DELETED —
        # иначе Obsidian и Graphify продолжали бы показывать забытое.
        mirror.unlink(missing_ok=True)
        session.flush()
        return AdminOutcome(status="forgotten", memory=memory,
                            text=f"Забыл: {memory.canonical_text}\n\n"
                                 f"Если понадобится — «Верни в память …».")

    if command.kind == "restore":
        memory.status = KnowledgeMemoryStatus.ACTIVE
        memory.updated_at = utcnow()
        _write_markdown_mirror(memory, vault_root=vault_root)
        session.flush()
        return AdminOutcome(status="restored", memory=memory,
                            text=f"Вернул: {memory.canonical_text}")

    if command.kind == "purge":
        forgotten_text = memory.canonical_text
        mirror.unlink(missing_ok=True)
        session.delete(memory)
        session.flush()
        return AdminOutcome(status="purged",
                            text=f"Удалил навсегда: {forgotten_text}\n\n"
                                 f"Это действие необратимо.")

    # fix: правим текст, а не заводим новую запись — иначе «исправь»
    # незаметно превращалось бы в «запомни ещё раз», и в памяти копились
    # бы обе версии.
    new_hash = compute_dedup_hash(command.replacement or "")
    clash = session.scalar(
        select(KnowledgeMemory).where(
            KnowledgeMemory.knowledge_user_id == knowledge_user_id,
            KnowledgeMemory.dedup_hash == new_hash,
            KnowledgeMemory.status == KnowledgeMemoryStatus.ACTIVE,
            KnowledgeMemory.id != memory.id,
        )
    )
    if clash is not None:
        return AdminOutcome(status="duplicate", memory=clash,
                            text=f"Такая запись уже есть: {clash.canonical_text}")

    memory.canonical_text = command.replacement or memory.canonical_text
    memory.dedup_hash = new_hash
    memory.tsv = func.to_tsvector("russian", memory.canonical_text)
    memory.updated_at = utcnow()
    session.flush()
    session.refresh(memory)
    _write_markdown_mirror(memory, vault_root=vault_root)
    return AdminOutcome(status="corrected", memory=memory,
                        text=f"Исправил: {memory.canonical_text}")
