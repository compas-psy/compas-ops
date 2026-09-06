"""Куда на диске ложатся файлы домена (ТЗ HELM v4.0 §14.16).

Одна функция и одно правило: у health-домена собственное файловое
дерево, у остальных — общий Vault.

Зачем отдельный модуль, а не ветка по месту. Путей, которые надо
развести, шесть (`ingest.py` дважды, `chat_intake.py`, `batch_intake.py`,
`atomizer.py`, `worker.py` через реконструкцию), и ветка «если health» в
каждом из них — ровно тот способ, которым уже был пропущен F15: строка
в БД уходила в health-схему, а файл заметки при этом ложился в общий
`<vault>/entities/`. Развилка живёт в одном месте, где её видно.

Правило вывода корня: `<vault>-private/health/users/<user_id>`. Приватный
корень выводится из общего, а не задаётся вторым независимым параметром —
иначе тестовая подмена `vault_root` на `tmp_path` перенаправляла бы
только половину записей, а вторая половина уходила бы в настоящий
`/opt/helm-knowledge-private` на машине разработчика.

Разделение включается тем же условием, что и маршрутизация строк в БД
(`is_health_domain` + `health_schema_configured`). Если health-схема не
настроена, ничего не разделяется: половинчатое состояние, где файлы уже
приватные, а текст ещё в общей схеме, хуже обоих цельных.

Чего это НЕ даёт (решение владельца 02.09.2026, `SPEC_DEVIATION.md`):
отдельного процесса-воркера для health. Воркер один и на общий Vault, и
на приватное дерево; разделение здесь — каталогом и правами, не
процессом.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from .health_schema import health_schema_configured, is_health_domain
from ..models import KnowledgeSource


def scope_root(vault_root: str, *, domain: str, knowledge_user_id: uuid.UUID | None) -> str:
    """Корень файлового дерева для этого домена и владельца."""
    if not (is_health_domain(domain) and health_schema_configured()):
        return vault_root
    return f"{vault_root.rstrip('/')}-private/health/users/{knowledge_user_id}"


def frontmatter(source: KnowledgeSource) -> str:
    """§14.3 markdown contract — обязательный YAML-блок для каждой
    нормализованной заметки. Собирается вручную, не через PyYAML: все
    значения — UUID/enum-строки/ISO-таймстемпы/hex-хэш, никогда
    свободный текст документа, экранирование не нужно, а зависимость не
    добавляется в Dockerfile.worker ради тривиального формата.

    Переехало сюда из `worker.py` 06.09.2026: заметку теперь пишут двое
    — воркер после разбора файла и `ingest_text()` для текста, — и
    формат обязан быть один. Две копии этой функции разошлись бы на
    первой же правке контракта.

    `confidence`/`supersedes`/`contradicts` спека резервирует под
    derived/L2 note — L1 SOURCE (`type: source`) всегда `primary`/
    `extracted`, ничего не supersedes; не заполняются пустыми
    значениями, а не пишутся вовсе.
    """
    return "\n".join([
        "---",
        f"id: {source.id}",
        "type: source",
        f"domain: {source.domain}",
        f"created_at: {source.created_at.isoformat()}",
        f"updated_at: {source.updated_at.isoformat()}",
        f'source_ids: ["{source.id}"]',
        f'source_sha256: ["{source.sha256}"]',
        f"sensitivity: {source.sensitivity}",
        f"trust: {source.trust}",
        f"status: {source.status}",
        "---",
        "",
        "",
    ])


def write_file(path: str, data: bytes) -> None:
    """Записать файл Vault атомарно: сначала временный, потом
    переименование. Оборванная на середине запись иначе оставила бы
    файл, который читается, но не совпадает с sha256 в базе, — а по
    этому совпадению §14.15 решает, отдавать ли оригинал."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)
