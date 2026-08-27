"""Конфигурация. Значения секретов приходят из /etc/helm/secrets, не из кода."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SECRETS_DIR = Path("/etc/helm/secrets")


def read_secret(name: str, default: str | None = None) -> str:
    """Прочитать секрет из файла 0600.

    Секреты не приходят через переменные окружения: окружение процесса
    читается из /proc любым процессом того же пользователя и попадает в
    дампы. Файл с правами 0600 root:root — нет (§6.3).
    """
    path = SECRETS_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    if default is not None:
        return default
    raise RuntimeError(f"секрет {name!r} не найден в {SECRETS_DIR}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HELM_", extra="ignore")

    database_url: str = "postgresql+psycopg://helm@/helm?host=/var/run/postgresql"
    policy_path: str = "/opt/helm/config/policies/actions.yaml"

    #: Telegram id владельца. Единственная identity, чьи решения принимаются.
    owner_id: str = ""

    #: RP ID для WebAuthn (§10.5.7). Привязан к домену намеренно: credential,
    #: выданный для helm.cmpas.ru, не сработает нигде больше.
    panel_rp_id: str = "helm.cmpas.ru"
    panel_origin: str = "https://helm.cmpas.ru"
    panel_session_ttl_hours: int = 24
    #: §10.5.8.1: challenge step-up живёт 60 секунд.
    stepup_challenge_ttl_seconds: int = 60

    environment: str = "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
