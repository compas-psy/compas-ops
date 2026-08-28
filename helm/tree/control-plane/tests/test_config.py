"""Разворачивание Docker secrets (`_FILE`) в Settings.

Найдено на реальном docker-compose bring-up (не в тестах): compose передаёт
секреты через `HELM_DATABASE_URL_FILE=/run/secrets/...` (конвенция Docker
secrets), а pydantic-settings эту конвенцию не реализует сам — переменная
молча игнорировалась, и приложение получало дефолтный URL из кода вместо
настоящего адреса контейнера Postgres. Раньше это было незаметно, потому что
Control Plane ни разу не запускался против реального compose — только против
тестовой БД, куда URL передавался напрямую.
"""

import os

import pytest

from helm_core.config import get_settings


@pytest.fixture(autouse=True)
def clean_env():
    keys = ["HELM_DATABASE_URL", "HELM_DATABASE_URL_FILE", "HELM_OWNER_ID",
            "HELM_OWNER_ID_FILE", "HELM_POLICY_PATH"]
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    os.environ["HELM_POLICY_PATH"] = "../config/policies/actions.yaml"
    get_settings.cache_clear()
    yield
    for k in keys:
        if saved[k] is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = saved[k]
    get_settings.cache_clear()


def test_file_suffixed_var_is_resolved(tmp_path):
    secret = tmp_path / "helm_database_url"
    secret.write_text("postgresql+psycopg://svc:pw@postgres:5432/helm\n", encoding="utf-8")
    os.environ["HELM_DATABASE_URL_FILE"] = str(secret)

    assert get_settings().database_url == "postgresql+psycopg://svc:pw@postgres:5432/helm"


def test_plain_var_wins_over_file_when_both_set(tmp_path):
    """Явное значение не должно быть тихо перезаписано файлом."""
    secret = tmp_path / "helm_database_url"
    secret.write_text("postgresql+psycopg://from-file@host/db\n", encoding="utf-8")
    os.environ["HELM_DATABASE_URL_FILE"] = str(secret)
    os.environ["HELM_DATABASE_URL"] = "postgresql+psycopg://from-plain@host/db"

    assert get_settings().database_url == "postgresql+psycopg://from-plain@host/db"


def test_missing_file_raises_clear_error():
    """Указанный, но не смонтированный секрет — явная ошибка, не тихий дефолт."""
    os.environ["HELM_DATABASE_URL_FILE"] = "/nonexistent/path/to/secret"

    with pytest.raises(RuntimeError, match="HELM_DATABASE_URL_FILE"):
        get_settings()


def test_no_file_var_falls_back_to_default():
    settings = get_settings()
    assert settings.database_url.startswith("postgresql+psycopg://")


def test_owner_id_also_supports_file_convention(tmp_path):
    secret = tmp_path / "telegram_owner_id"
    secret.write_text("100500\n", encoding="utf-8")
    os.environ["HELM_OWNER_ID_FILE"] = str(secret)

    assert get_settings().owner_id == "100500"
