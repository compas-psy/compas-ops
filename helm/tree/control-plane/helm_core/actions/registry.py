"""Реестр действий (ТЗ §8.3).

Каждое действие: типизированный Pydantic-вход → канонизация → SHA256 →
preconditions → idempotency key → audit. Реестр не исполняет ничего сам и
не знает про approvals: он только описывает, что можно исполнить и как это
однозначно назвать.

Разделение намеренное. Executor вызывается из approvals/service.py уже после
проверки уровня, хэша, TTL и preconditions; если бы реестр умел исполнять
сам, появился бы второй путь исполнения в обход §8.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ValidationError

from .canonical import action_hash
from .policy import ActionPolicy, Policy


class PreconditionFailed(RuntimeError):
    """Предусловие не выполнено — сейчас или уже не выполняется."""

    def __init__(self, name: str, detail: str = ""):
        self.name = name
        self.detail = detail
        super().__init__(f"precondition {name!r} не выполнено: {detail}" if detail else
                         f"precondition {name!r} не выполнено")


class UnknownAction(KeyError):
    """Действие не зарегистрировано."""


class ExecutionContext(Protocol):
    """То, что executor получает от Control Plane. Секретов здесь нет.

    §8.3: payload действия никогда не содержит значений секретов, только
    reference ID; настоящие значения executor берёт из runtime-хранилища.
    """

    approval_id: str | None
    task_id: str | None
    idempotency_key: str


PreconditionCheck = Callable[[BaseModel, ExecutionContext], None]


@dataclass(frozen=True)
class RegisteredAction:
    action_type: str
    model: type[BaseModel]
    executor: Callable[[BaseModel, ExecutionContext], Any]
    preconditions: dict[str, PreconditionCheck]

    def parse(self, payload: dict[str, Any]) -> BaseModel:
        try:
            return self.model.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"{self.action_type}: payload не проходит типизацию: {exc}") from exc

    def canonical_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Payload после типизации — то, что уходит в хэш.

        Хэшируется именно нормализованный вид, а не то, что прислал Hermes:
        иначе лишнее поле или другой порядок ключей дали бы другой хэш при
        том же фактическом действии, и наоборот — отброшенное при валидации
        поле не влияло бы на хэш, хотя влияет на смысл.
        """
        return self.parse(payload).model_dump(mode="json", exclude_none=False)

    def hash_of(self, payload: dict[str, Any]) -> str:
        return action_hash(self.action_type, self.canonical_payload(payload))


class ActionRegistry:
    def __init__(self, policy: Policy):
        self._policy = policy
        self._actions: dict[str, RegisteredAction] = {}

    def action(
        self,
        action_type: str,
        *,
        model: type[BaseModel],
        preconditions: dict[str, PreconditionCheck] | None = None,
    ):
        """Декоратор регистрации (§8.3).

        Уровень здесь не указывается намеренно: он берётся из policy-файла.
        Позволить коду объявить свой level означало бы два источника истины,
        и тот, что в коде, менялся бы агентом без одобрения.
        """

        def wrap(fn: Callable[[BaseModel, ExecutionContext], Any]):
            if action_type in self._actions:
                raise ValueError(f"действие {action_type!r} уже зарегистрировано")
            spec = self._policy.get(action_type)  # незнакомое policy — ошибка при импорте
            checks = dict(preconditions or {})
            missing = set(spec.required_preconditions) - set(checks)
            if missing:
                raise ValueError(
                    f"{action_type}: policy требует preconditions {sorted(missing)}, "
                    f"но они не реализованы"
                )
            self._actions[action_type] = RegisteredAction(
                action_type=action_type, model=model, executor=fn, preconditions=checks
            )
            return fn

        return wrap

    def get(self, action_type: str) -> RegisteredAction:
        try:
            return self._actions[action_type]
        except KeyError:
            raise UnknownAction(f"действие {action_type!r} не зарегистрировано")

    def policy_for(self, action_type: str) -> ActionPolicy:
        return self._policy.get(action_type)

    def check_preconditions(self, action_type: str, payload: dict[str, Any], ctx: ExecutionContext) -> None:
        """Проверка предусловий, перечисленных в policy.

        Вызывается дважды: при propose и повторно непосредственно перед
        исполнением (§8.4). Второй вызов — не избыточность: между одобрением
        и исполнением проходит до 24 часов, за которые CI может покраснеть,
        а цена измениться.
        """
        registered = self.get(action_type)
        spec = self._policy.get(action_type)
        parsed = registered.parse(payload)
        for name in spec.required_preconditions:
            registered.preconditions[name](parsed, ctx)

    def known_types(self) -> list[str]:
        return sorted(self._actions)
