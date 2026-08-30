"""Действия над аккаунтами Второго мозга, проходящие через реестр.

Пока здесь одно действие — необратимое удаление аккаунта
KNOWLEDGE_USER. Решение учредителя от 30.08.2026: удаление чужих личных
данных обязано идти через RED-реестр, а не «тремя защитами в
обработчике». Разница не в количестве препятствий, а в том, кто и когда
подтверждает: RED означает, что действие физически невозможно исполнить
без записанного одобрения владельца, и это одобрение видно в панели, в
Telegram и в аудите отдельной строкой.

Предложить удаление и одобрить его — два разных момента и два разных
подтверждения. Приостановка аккаунта остаётся обязательной третьей
ступенью и проверяется дважды: при предложении и ещё раз прямо перед
исполнением, потому что между ними может пройти до двух часов.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel

from ..knowledge.offboarding import DeleteRefused, delete_user_permanently
from ..models import KnowledgeUser, KnowledgeUserStatus
from .registry import ActionRegistry, ExecutionContext, PreconditionFailed


class DeleteKnowledgeUserParams(BaseModel):
    knowledge_user_id: uuid.UUID
    #: Владелец обязан явно сказать, забрана выгрузка или сознательно не
    #: нужна. Отказ от выгрузки — тоже решение, но его надо принять, а не
    #: проскочить. Поле входит в хэш действия: подменить его между
    #: одобрением и исполнением нельзя.
    export_taken: bool


def register_knowledge_actions(registry: ActionRegistry) -> ActionRegistry:
    def user_suspended(params: DeleteKnowledgeUserParams, ctx: ExecutionContext) -> None:
        """Удалять можно только заранее приостановленный аккаунт.

        Проверка стоит здесь, а не только внутри `delete_user_permanently`,
        именно ради второго прогона перед исполнением: аккаунт могли
        вернуть в строй после того, как удаление предложили.
        """
        session = getattr(ctx, "session", None)
        if session is None:
            raise PreconditionFailed("user_suspended", "нет сессии БД для проверки")
        user = session.get(KnowledgeUser, params.knowledge_user_id)
        if user is None:
            raise PreconditionFailed("user_suspended", "пользователь не найден")
        if user.status != KnowledgeUserStatus.SUSPENDED:
            raise PreconditionFailed(
                "user_suspended",
                f"статус {user.status}, требуется SUSPENDED — "
                "сначала приостановите доступ")

    def export_decided(params: DeleteKnowledgeUserParams, ctx: ExecutionContext) -> None:
        if not params.export_taken:
            raise PreconditionFailed(
                "export_decided",
                "подтвердите, что выгрузка забрана или сознательно не нужна")

    @registry.action(
        "delete_knowledge_user",
        model=DeleteKnowledgeUserParams,
        preconditions={"user_suspended": user_suspended,
                       "export_decided": export_decided},
    )
    def delete_knowledge_user(params: DeleteKnowledgeUserParams,
                              ctx: ExecutionContext) -> dict[str, Any]:
        session = getattr(ctx, "session", None)
        if session is None:
            raise RuntimeError("исполнение удаления требует сессии БД")
        try:
            result = delete_user_permanently(session, params.knowledge_user_id)
        except DeleteRefused as exc:
            # Отказ хранилища — это провал исполнения, а не «успешно ничего
            # не сделали». Одобрение обязано уйти в FAILED, а не в EXECUTED.
            raise RuntimeError(str(exc)) from exc
        return {
            "knowledge_user_id": str(params.knowledge_user_id),
            "rows_deleted": result.rows_deleted,
            "files_removed": result.files_removed,
            "backup_retention": result.retention_notice,
        }

    return registry
