"""v3.8 §14.3 — выгрузка своего архива и окончательное удаление.

Обратная сторона `onboarding.py`. Порядок из спеки:

    suspend → optional user vault export → explicit RED delete if requested

Каждый шаг отдельный и явный. «Deleting account never happens merely
because Telegram identity was revoked» — отзыв доступа и уничтожение
данных здесь никогда не одно действие.

Почему удаление НЕ идёт через approval engine, хотя спека называет его
RED. Approval engine существует для случая «агент хочет сделать —
владелец подписывает». Здесь инициатор сам владелец, лично, из своей
панели, со свежим passkey. Провести это через «агент предложил →
владелец одобрил» значило бы завести путь, которым агент МОЖЕТ
предложить уничтожение чужого аккаунта, — а сейчас такого пути нет
вовсе, и это сильнее любой записи одобрения. Защита здесь другая, но не
слабее: аккаунт обязан быть заранее приостановлен (два разнесённых во
времени решения, не одно), passkey привязан именно к этому
пользователю, и владелец обязан явно сказать, забрал он выгрузку или
сознательно отказался от неё. Решение вынесено владельцу в
V3.8-DELTA.md — если он предпочтёт полный RED-путь через реестр
действий, переделка локальная.
"""

from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .health_schema import delete_all_for_user, health_schema_configured
from .ingest import DEFAULT_VAULT_ROOT
from .tenancy import bind_knowledge_user, knowledge_principal
from .vault import scope_root
from ..models import (
    KnowledgeAnswerRun, KnowledgeBatchItem, KnowledgeChannelIdentity, KnowledgeChunk,
    KnowledgeIngestBatch, KnowledgeIngestJob, KnowledgeInvite, KnowledgeMemory,
    KnowledgeDomain, KnowledgePendingAttachment, KnowledgeRelation, KnowledgeSource, KnowledgeUser,
    KnowledgeUserStatus, KnowledgeUserUsage, PanelEnrollmentToken, PanelSession,
    WebauthnCredential,
)
from ..models.base import utcnow

#: §14.3 "backup-retention disclosure". Числа обязаны совпадать с
#: `restic forget` в `scripts/backup.sh` — за этим следит отдельный тест,
#: потому что расхождение здесь означает, что человеку назвали неверный
#: срок, а это хуже, чем не назвать никакого.
BACKUP_KEEP_DAILY = 7
BACKUP_KEEP_WEEKLY = 4
BACKUP_KEEP_MONTHLY = 6

#: Самый долгий срок, в течение которого данные ещё могут лежать в
#: зашифрованном бэкапе после удаления. Шесть месячных снимков (§26.3) —
#: верхняя граница; называем именно её, а не среднюю, чтобы обещание было
#: выполнимым, а не оптимистичным.
BACKUP_RETENTION_NOTICE = (
    f"После удаления данные исчезают из рабочей системы сразу. В "
    f"зашифрованных резервных копиях они могут сохраняться до "
    f"{BACKUP_KEEP_MONTHLY} месяцев: политика хранения — "
    f"{BACKUP_KEEP_DAILY} ежедневных, {BACKUP_KEEP_WEEKLY} еженедельных, "
    f"{BACKUP_KEEP_MONTHLY} ежемесячных снимков. Копии не используются "
    f"для ответов и доступны только владельцу системы при восстановлении."
)


def user_vault_dir(vault_root: str, knowledge_user_id: uuid.UUID) -> Path:
    return Path(vault_root) / "users" / str(knowledge_user_id)


@dataclass
class ExportResult:
    archive_path: Path
    memories: int
    sources: int
    bytes_written: int


def export_user_vault(session: Session, knowledge_user_id: uuid.UUID, *,
                      out_dir: str | Path,
                      vault_root: str | None = None) -> ExportResult:
    """Собрать ZIP со всем, что человек положил в свой Второй мозг.

    Файл кладётся на диск и НЕ отдаётся в интерфейс владельца: §14.3
    запрещает владельцу обычный просмотр чужого содержимого, а выгрузка
    существует, чтобы отдать данные их хозяину, а не чтобы дать их
    прочитать кому-то ещё. Владелец может создать архив (иначе после
    приостановки его некому создать — человек уже не войдёт) и передать
    файл, но панель его не рендерит.

    Оригинальные файлы (L0 RAW) в архив НЕ кладутся: это гигабайты, и
    выгрузка превращалась бы в копию всего хранилища. Вместо них — полный
    перечень с именами, размерами и SHA256, по которому любой конкретный
    файл выдаётся отдельно. Названо в манифесте прямо, чтобы человек не
    решил, будто получил всё.
    """
    vault_root = vault_root or DEFAULT_VAULT_ROOT
    tenant_id = bind_knowledge_user(session, knowledge_user_id)
    user = session.get(KnowledgeUser, tenant_id)

    memories = session.scalars(
        select(KnowledgeMemory)
        .where(KnowledgeMemory.knowledge_user_id == tenant_id)
        .order_by(KnowledgeMemory.created_at)
    ).all()
    sources = session.scalars(
        select(KnowledgeSource)
        .where(KnowledgeSource.knowledge_user_id == tenant_id)
        .order_by(KnowledgeSource.created_at)
    ).all()

    manifest = {
        "knowledge_user_id": str(tenant_id),
        "display_name": user.display_name if user else None,
        "timezone": user.timezone if user else None,
        "generated_at": utcnow().isoformat(),
        "memories_count": len(memories),
        "sources_count": len(sources),
        "backup_retention": BACKUP_RETENTION_NOTICE,
        "raw_originals_included": False,
        "raw_originals_note": (
            "Оригинальные файлы в архив не включены из-за размера. "
            "Полный перечень — в sources.json, каждый файл выдаётся отдельно "
            "по запросу."
        ),
        "memories": [
            {"id": str(m.id), "kind": m.kind, "text": m.canonical_text,
             "status": m.status, "created_at": m.created_at.isoformat(),
             "expires_at": m.expires_at.isoformat() if m.expires_at else None}
            for m in memories
        ],
    }
    sources_index = [
        {"id": str(s.id), "original_filename": s.original_filename, "domain": s.domain,
         "sha256": s.sha256, "mime_type": s.mime_type, "status": s.status,
         "created_at": s.created_at.isoformat()}
        for s in sources
    ]

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    archive_path = out_dir / f"knowledge-export-{tenant_id}-{stamp}.zip"

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json",
                         json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("sources.json",
                         json.dumps(sources_index, ensure_ascii=False, indent=2))
        # Markdown-зеркала памяти — ровно те файлы, что читает Obsidian.
        memory_dir = user_vault_dir(vault_root, tenant_id) / "memory"
        if memory_dir.is_dir():
            for path in sorted(memory_dir.glob("*.md")):
                archive.write(path, f"memory/{path.name}")
        # Разобранный текст документов (L1), если разбор дошёл до файла.
        for source in sources:
            if not source.source_path:
                continue
            path = Path(source.source_path)
            if path.is_file():
                archive.write(path, f"sources/{path.name}")

    return ExportResult(archive_path=archive_path, memories=len(memories),
                        sources=len(sources),
                        bytes_written=archive_path.stat().st_size)


#: Порядок важен: сначала то, что ссылается, потом то, на что ссылаются.
#: Каскадов в схеме нет намеренно — «удалить одну строку и потерять
#: половину базы» не должно быть возможно случайно.
_TENANT_CONTENT_TABLES = (
    KnowledgeAnswerRun, KnowledgeRelation, KnowledgeChunk, KnowledgeBatchItem,
    KnowledgeIngestJob, KnowledgeIngestBatch, KnowledgePendingAttachment,
    KnowledgeMemory, KnowledgeSource,
)


@dataclass
class DeleteResult:
    rows_deleted: int
    files_removed: bool
    retention_notice: str


class DeleteRefused(Exception):
    """Удаление отклонено предусловием, а не сломалось."""


def delete_user_permanently(session: Session, knowledge_user_id: uuid.UUID, *,
                            vault_root: str | None = None) -> DeleteResult:
    """Необратимо уничтожить содержимое Второго мозга и закрыть аккаунт.

    Предусловие: аккаунт уже приостановлен. Спека задаёт порядок
    `suspend → export → delete`, и требование именно приостановки
    заранее делает удаление ДВУМЯ разнесёнными решениями, а не одним
    движением руки в интерфейсе.

    Строка самого пользователя остаётся с пометкой `DELETED`, а не
    исчезает: это надгробие. Оно держит внешние ключи, отвечает на
    вопрос «кто и когда был удалён» и не даёт переиспользовать тот же
    идентификатор. Содержимого за ним больше нет.
    """
    vault_root = vault_root or DEFAULT_VAULT_ROOT
    user = session.get(KnowledgeUser, knowledge_user_id)
    if user is None:
        raise DeleteRefused("пользователь не найден")
    if user.status == KnowledgeUserStatus.DELETED:
        raise DeleteRefused("уже удалён")
    if user.status != KnowledgeUserStatus.SUSPENDED:
        raise DeleteRefused(
            "сначала приостановите доступ — удаление это отдельное второе решение")

    tenant_id = bind_knowledge_user(session, knowledge_user_id)
    rows = 0
    for table in _TENANT_CONTENT_TABLES:
        result = session.execute(
            delete(table).where(table.knowledge_user_id == tenant_id))
        rows += result.rowcount or 0

    session.execute(delete(KnowledgeUserUsage)
                    .where(KnowledgeUserUsage.knowledge_user_id == tenant_id))
    session.execute(delete(KnowledgeChannelIdentity)
                    .where(KnowledgeChannelIdentity.knowledge_user_id == tenant_id))
    session.execute(delete(KnowledgeInvite)
                    .where(KnowledgeInvite.knowledge_user_id == tenant_id))

    principal = knowledge_principal(tenant_id)
    session.execute(delete(PanelSession).where(PanelSession.owner_id == principal))
    session.execute(delete(PanelEnrollmentToken)
                    .where(PanelEnrollmentToken.owner_id == principal))
    session.execute(delete(WebauthnCredential)
                    .where(WebauthnCredential.owner_id == principal))

    # health живёт в собственной схеме и собственном дереве — ни то, ни
    # другое не покрывается списком public-таблиц и `users/<uid>/` ниже.
    # До переноса R1 это было невидимо: health-таблицы стояли пустыми.
    #
    # Условие то же, что у самого разделения. Без него `scope_root()` для
    # ненастроенной health-схемы честно возвращает ОБЩИЙ корень Vault —
    # и следующий цикл снёс бы его целиком, вместе с данными других
    # владельцев. Поймано тестом test_delete_does_not_touch_another_user.
    directories = [user_vault_dir(vault_root, tenant_id)]
    if health_schema_configured():
        rows += delete_all_for_user(knowledge_user_id=tenant_id)
        directories.append(Path(scope_root(vault_root, domain=KnowledgeDomain.HEALTH,
                                           knowledge_user_id=tenant_id)))

    files_removed = False
    for directory in directories:
        if directory.is_dir():
            shutil.rmtree(directory)
            files_removed = True

    user.status = KnowledgeUserStatus.DELETED
    user.display_name = None
    session.flush()

    return DeleteResult(rows_deleted=rows, files_removed=files_removed,
                        retention_notice=BACKUP_RETENTION_NOTICE)
