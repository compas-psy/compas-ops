"""Точка входа контейнера helm-knowledge-worker (`Dockerfile.worker`).

Тот же паттерн подключения к БД, что и `app.py::create_app` (engine с
`pool_pre_ping`, `sessionmaker(expire_on_commit=False)`) — отдельный
процесс, не отдельная логика конфигурации.
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..config import get_settings
from .worker import run_forever


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    run_forever(session_factory)


if __name__ == "__main__":
    main()
