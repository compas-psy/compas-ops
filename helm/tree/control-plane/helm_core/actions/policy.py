"""Policy: уровни действий и их границы (ТЗ §8.1, §8.2, §8.7).

Policy — детерминированный файл, не промпт. LLM не имеет права понизить
уровень действия (§8.2), поэтому единственный вход сюда — YAML на диске,
и никакая часть runtime не может поднять уровень выше или опустить ниже
границ, записанных здесь.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml


class Level(enum.IntEnum):
    """Порядок важен: сравнение уровней — часть контроля понижения."""

    GREEN = 0
    YELLOW = 1
    RED = 2

    @classmethod
    def parse(cls, raw: str) -> "Level":
        try:
            return cls[str(raw).strip().upper()]
        except KeyError:
            raise PolicyError(f"неизвестный уровень {raw!r}; допустимы GREEN|YELLOW|RED")


class PolicyError(ValueError):
    """Policy-файл противоречив или запрошено недопустимое изменение."""


#: Действия, которые не повышают доверие автоматически ни при каком числе
#: успешных исполнений (§8.7). Список закрыт и живёт в коде, а не в YAML:
#: policy-файл редактируется агентом по одобрению, а этот инвариант — нет.
NEVER_GRADUATE = frozenset(
    {
        "spend_money",
        "legal_submission",
        "delete_business_data",
        "change_price",
        "signalai_live_execution",
        "secret_rotation",
        "change_policy",
        "change_trust_level",
    }
)


@dataclass(frozen=True)
class ActionPolicy:
    action_type: str
    initial_level: Level
    minimum_allowed_level: Level
    approval_ttl: timedelta
    required_preconditions: tuple[str, ...]
    title_ru: str
    panel_view: str

    def __post_init__(self) -> None:
        if self.minimum_allowed_level > self.initial_level:
            raise PolicyError(
                f"{self.action_type}: minimum_allowed_level "
                f"({self.minimum_allowed_level.name}) выше initial_level "
                f"({self.initial_level.name})"
            )
        if self.panel_view not in PANEL_VIEWS:
            raise PolicyError(
                f"{self.action_type}: panel_view={self.panel_view!r}; "
                f"допустимы {sorted(PANEL_VIEWS)}"
            )

    @property
    def never_graduates(self) -> bool:
        return self.action_type in NEVER_GRADUATE

    def check_demotion(self, target: Level) -> None:
        """Понижение уровня допустимо только до minimum_allowed_level."""
        if target < self.minimum_allowed_level:
            raise PolicyError(
                f"{self.action_type}: понижение до {target.name} запрещено, "
                f"минимум — {self.minimum_allowed_level.name}"
            )
        if self.never_graduates and target < self.initial_level:
            raise PolicyError(
                f"{self.action_type}: входит в закрытый список §8.7 и не понижается"
            )


PANEL_VIEWS = frozenset({"generic", "publication", "git_merge", "spend", "deploy"})

#: TTL по умолчанию (§8.4). spend_money сверх порога переопределяет его в YAML.
DEFAULT_APPROVAL_TTL = timedelta(hours=24)


def _parse_ttl(raw: Any, action_type: str) -> timedelta:
    if raw is None:
        return DEFAULT_APPROVAL_TTL
    text = str(raw).strip().lower()
    if not text or text[-1] not in "hm" or not text[:-1].isdigit():
        raise PolicyError(f"{action_type}: approval_ttl={raw!r}; ожидается вида '24h' или '120m'")
    value = int(text[:-1])
    if value <= 0:
        raise PolicyError(f"{action_type}: approval_ttl должен быть положительным")
    return timedelta(hours=value) if text[-1] == "h" else timedelta(minutes=value)


class Policy:
    """Загруженный actions.yaml. Только чтение."""

    def __init__(self, actions: dict[str, ActionPolicy]):
        self._actions = actions

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        entries = raw.get("actions") or {}
        if not entries:
            raise PolicyError(f"{path}: секция actions пуста")

        actions: dict[str, ActionPolicy] = {}
        for action_type, spec in entries.items():
            spec = spec or {}
            initial = Level.parse(spec.get("initial_level", "RED"))
            minimum = Level.parse(spec.get("minimum_allowed_level", spec.get("initial_level", "RED")))
            actions[action_type] = ActionPolicy(
                action_type=action_type,
                initial_level=initial,
                minimum_allowed_level=minimum,
                approval_ttl=_parse_ttl(spec.get("approval_ttl"), action_type),
                required_preconditions=tuple(spec.get("required_preconditions") or ()),
                title_ru=spec.get("title_ru") or action_type,
                panel_view=spec.get("panel_view") or "generic",
            )
        return cls(actions)

    def get(self, action_type: str) -> ActionPolicy:
        """Неизвестное действие — не GREEN по умолчанию.

        Отсутствие записи в policy означает, что действие никто не оценивал.
        Разрешить его автоматически было бы способом обойти §8: достаточно
        зарегистрировать action под новым именем.
        """
        try:
            return self._actions[action_type]
        except KeyError:
            raise PolicyError(
                f"действие {action_type!r} отсутствует в policy — исполнение запрещено"
            )

    def __contains__(self, action_type: str) -> bool:
        return action_type in self._actions

    def __len__(self) -> int:
        return len(self._actions)
