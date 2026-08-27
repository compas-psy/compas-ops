"""Фикстурные действия для P2 (ТЗ §31 P2: «action registry with 2–3 fixture actions»).

Три штуки покрывают три уровня и три поведения preconditions:

- `notify_owner`      GREEN, без предусловий — проверяет, что безопасное
                      действие исполняется без одобрения;
- `kanban_snapshot`   YELLOW, обратимое — проверяет, что оно тоже идёт без
                      одобрения, но попадает в audit;
- `publish_public_content` RED с двумя предусловиями — проверяет и запрет
                      без одобрения, и перепроверку предусловий перед
                      исполнением.

Исполнители намеренно ничего не делают наружу: до P4/P7 у HELM ещё нет ни
канала публикации, ни Kanban. Фикстура, которая «почти публикует», — это
способ однажды опубликовать по-настоящему во время теста.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .policy import Policy
from .registry import ActionRegistry, ExecutionContext, PreconditionFailed


class NotifyOwnerParams(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    urgent: bool = False


class KanbanSnapshotParams(BaseModel):
    reason: str = Field(min_length=1, max_length=280)


class PublishParams(BaseModel):
    channel: str = Field(min_length=1, max_length=64)
    body: str = Field(min_length=1)


#: Каналы, в которые вообще разрешено публиковать. Пустой набор означает,
#: что публикация невозможна физически, а не «не настроена»: до P10 ни один
#: канал не подтверждён владельцем.
ALLOWED_PUBLIC_CHANNELS: set[str] = set()

#: Запреты бренда (устав §5.6, ТЗ §10). Метрика, выросшая за счёт нарушения,
#: считается не выросшей, поэтому проверка стоит в предусловии, а не в ревью.
BANNED_BRAND_PATTERNS = (
    "streak", "не пропусти", "ты пропустил", "осталось часов",
    "гарантирую результат", "вылечит",
)


def register_fixtures(registry: ActionRegistry) -> ActionRegistry:
    @registry.action("notify_owner", model=NotifyOwnerParams)
    def notify_owner(params: NotifyOwnerParams, ctx: ExecutionContext) -> dict[str, Any]:
        return {"delivered": False, "queued": True, "urgent": params.urgent}

    @registry.action("kanban_snapshot", model=KanbanSnapshotParams)
    def kanban_snapshot(params: KanbanSnapshotParams, ctx: ExecutionContext) -> dict[str, Any]:
        return {"snapshot": ctx.idempotency_key[:16], "reason": params.reason}

    def channel_allowlisted(params: PublishParams, ctx: ExecutionContext) -> None:
        if params.channel not in ALLOWED_PUBLIC_CHANNELS:
            raise PreconditionFailed("channel_allowlisted",
                                     f"канал {params.channel!r} не в allowlist")

    def brand_rules_checked(params: PublishParams, ctx: ExecutionContext) -> None:
        lowered = params.body.casefold()
        for pattern in BANNED_BRAND_PATTERNS:
            if pattern in lowered:
                raise PreconditionFailed("brand_rules_checked",
                                         f"текст содержит запрещённый приём {pattern!r}")

    @registry.action(
        "publish_public_content",
        model=PublishParams,
        preconditions={"channel_allowlisted": channel_allowlisted,
                       "brand_rules_checked": brand_rules_checked},
    )
    def publish_public_content(params: PublishParams, ctx: ExecutionContext) -> dict[str, Any]:
        return {"published": True, "channel": params.channel, "chars": len(params.body)}

    return registry


def build_registry(policy_path: str) -> ActionRegistry:
    return register_fixtures(ActionRegistry(Policy.load(policy_path)))
