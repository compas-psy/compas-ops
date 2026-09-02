"""Конфигурация. Значения секретов приходят из Docker secrets, не из кода."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: НАЙДЕНО НА P4 bring-up: реальный путь монтирования Docker secrets внутри
#: контейнера — /run/secrets (стандарт Docker), не /etc/helm/secrets (это
#: путь ХОСТА, где лежат исходники секретов до монтирования). До этой
#: правки read_secret() внутри контейнера всегда бил в default="" — hermes
#: _service_hmac резолвился в пустую строку, и HMAC на /internal/* фактически
#: не проверял подпись (пустой секрет тривиально подделать).
SECRETS_DIR = Path("/run/secrets")

#: Поля Settings, для которых docker-compose.yml передаёт значение через
#: Docker secret — переменной с суффиксом _FILE, содержащей путь к файлу,
#: а не сам секрет напрямую (обычная практика Docker secrets, как
#: POSTGRES_PASSWORD_FILE у официального образа Postgres).
_FILE_BACKED_FIELDS = ("database_url", "owner_id", "max_owner_id", "health_database_url")


def _resolve_file_env_vars(prefix: str) -> None:
    """Развернуть HELM_*_FILE в HELM_* до создания Settings.

    pydantic-settings не реализует конвенцию `_FILE` сам — если этого не
    сделать, `HELM_DATABASE_URL_FILE=/run/secrets/helm_database_url` тихо
    игнорируется, а `database_url` откатывается на дефолт из кода, который
    не подходит для контейнера. Раньше это не было замечено, потому что
    Control Plane ни разу не запускался против реального docker-compose —
    только против тестовой БД с URL, переданным напрямую через
    HELM_TEST_DB/окружение теста.
    """
    for field in _FILE_BACKED_FIELDS:
        file_var = f"{prefix}{field.upper()}_FILE"
        plain_var = f"{prefix}{field.upper()}"
        path = os.environ.get(file_var)
        if not path or os.environ.get(plain_var):
            continue
        try:
            os.environ[plain_var] = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(
                f"{file_var}={path!r} указан, но файл не читается: {exc}"
            ) from exc


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

    #: ADR-005/P12 — отдельная роль `helm_health`, отдельная схема
    #: `health`, та же база `helm`. Пусто по умолчанию: `scripts/setup-
    #: health-role.sh` — ручной, идемпотентный шаг (тот же класс, что уже
    #: есть у `compose/post-migration.sql`), до его прогона на сервере
    #: `knowledge/health_schema.py` держит health-путь выключенным
    #: (fail-open на прежнее поведение — domain=health в `public`,
    #: отфильтрован в probe()), а не падает.
    health_database_url: str = ""

    #: Telegram id владельца. Единственная identity, чьи решения принимаются.
    owner_id: str = ""

    #: Числовой id владельца в MAX. Это ДРУГОЕ число, чем `owner_id`:
    #: мессенджеры не разделяют пространство идентификаторов. Нужен, чтобы
    #: вебхук MAX мог отличить владельца от постороннего (§10.1 п.2);
    #: задачи при этом регистрируются под канонической identity `owner_id`,
    #: иначе cross-channel дедуп (§10.4) не сработал бы никогда — хэш
    #: намерения считается вместе с owner_id.
    max_owner_id: str = ""

    #: v3.8 §9.0/P8.6.2 — публичное имя бота (видно в его собственном
    #: профиле, не секрет), нужно только для сборки deep-link
    #: `https://t.me/<username>?start=kb_<token>`. Токен/webhook-секрет
    #: самого бота — файлы `read_secret()` (app.py), не Settings.
    knowledge_telegram_bot_username: str = ""

    #: RP ID для WebAuthn (§10.5.7). Привязан к домену намеренно: credential,
    #: выданный для helm.cmpas.ru, не сработает нигде больше.
    panel_rp_id: str = "helm.cmpas.ru"
    panel_origin: str = "https://helm.cmpas.ru"
    panel_session_ttl_hours: int = 24
    #: §10.5.8.1: challenge step-up живёт 60 секунд.
    stepup_challenge_ttl_seconds: int = 60

    environment: str = "production"

    #: R4 (§14.18): раньше модель извлекателя была вшита в код как
    #: `DEFAULT_MODEL = "gemma2:2b"` — неявным архитектурным решением, а не
    #: явным выбором. Победитель бенчмарка R4 задаётся здесь, а не правкой
    #: `semantic_extract.py`.
    knowledge_semantic_model: str = "gemma2:2b"

    #: R4 (§14.18): `OLLAMA_KEEP_ALIVE=0` в compose выгружает модель после
    #: каждого запроса — годится для редких ручных вызовов, но не измерено
    #: для семантического извлечения по окнам, идущим одно за другим.
    #: Значение здесь — production policy, выбранная замером RAM/latency в
    #: R4, а не вкусом; формат — как принимает Ollama `keep_alive`
    #: (`"0"`, `"5m"`, ...).
    knowledge_semantic_keep_alive: str = "0"


@lru_cache
def get_settings() -> Settings:
    _resolve_file_env_vars("HELM_")
    return Settings()
