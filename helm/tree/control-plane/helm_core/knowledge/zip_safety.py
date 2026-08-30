"""Безопасная потоковая распаковка ZIP (v3.7 §14.7.6, `V3.7-DELTA.md`).

ZIP — контейнер, не формат документа парсера (§14.4.0: "ZIP must no
longer be treated as a MarkItDown document format"). Эта обработка
происходит ДО роутера парсеров (`parsers.py`), а не через него.

Два прохода, оба обязательны:

1. `preflight()` — читает только central directory (заголовки), без
   распаковки байт. Решает по каждому члену: eligible / quarantine /
   skipped_unsupported / skipped_nested_archive. Проблемы уровня всего
   архива (битый ZIP, шифрование, превышение общих лимитов) поднимают
   `ArchiveBlocked` — §14.7.6: "No partial extraction; tell owner which
   limit was hit."
2. `extract_member()` — реальное потоковое чтение ОДНОГО eligible-члена,
   вызывается только после того, как владелец выбрал домен (§14.5.1).
   Заявленным в central directory размерам не доверяем — считаем реальные
   байты по мере чтения (§14.7.6: "Declared ZIP metadata is not
   trusted"), CRC проверяет сам `zipfile` при дочтении до EOF.

Пути членов архива — ТОЛЬКО метаданные (`archive_member_path_original`),
никогда не становятся путём на диске напрямую (anti zip-slip) — child RAW
адресуется по `source_id`/`sha256`, как и у одиночных вложений.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import stat
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

#: Дефолты §14.7.6. Хардкод, не `config/knowledge.yaml` — ни один другой
#: параметр Knowledge сегодня не живёт в YAML (см. MAX_ATTACHMENT_BYTES в
#: chat_intake.py) — тот же паттерн, отдельный механизм конфигурации ради
#: одного модуля не оправдан (CLAUDE.md §2).
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 500
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBER_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_PATH_DEPTH = 10

_JUNK_PREFIXES = ("__MACOSX/",)
_JUNK_NAMES = {".DS_Store", "Thumbs.db"}

#: v1: нет рекурсивной распаковки — вложенный архив просто пропускается
#: (§14.7.6 "Nested archives" — "avoids recursive archive bombs").
_NESTED_ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"}

#: Исполняемые/программные форматы — никогда не запускаются, только
#: SKIPPED_UNSUPPORTED (§14.7.6 "MIME and executable safety").
_EXECUTABLE_EXTS = {".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".sh",
                    ".ps1", ".com", ".msi", ".app", ".jar", ".apk"}

READ_CHUNK_BYTES = 1024 * 1024


class ArchiveBlocked(Exception):
    """Проблема уровня всего архива — batch получает status=BLOCKED целиком,
    ни один член не обрабатывается частично (§14.7.6: "No partial
    extraction; tell owner which limit was hit")."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass
class MemberDecision:
    ordinal: int
    path_original: str
    path_normalized: str
    declared_compressed_size: int
    declared_uncompressed_size: int
    #: "eligible" | "quarantine" | "skipped_unsupported" | "skipped_nested_archive"
    status: str
    reason: str | None = None
    #: По расширению (`mimetypes`, stdlib) — не magic-byte sniffing.
    #: Упрощение: ни один другой модуль в кодовой базе сегодня не делает
    #: содержимого-based детекцию (нет python-magic/аналога), заводить
    #: новую зависимость ради этого поля не оправдано (CLAUDE.md §2).
    detected_mime: str | None = None

    @property
    def eligible(self) -> bool:
        return self.status == "eligible"


def _is_junk(name: str) -> bool:
    if name in _JUNK_NAMES or Path(name).name in _JUNK_NAMES:
        return True
    return any(name.startswith(p) for p in _JUNK_PREFIXES)


def _is_unsafe_path(raw_name: str) -> bool:
    """zip-slip: абсолютные пути, `..`, диски Windows, NUL-байты."""
    if raw_name.startswith("/") or raw_name.startswith("\\"):
        return True
    if len(raw_name) > 1 and raw_name[1] == ":":  # C:\..., D:/...
        return True
    if "\x00" in raw_name:
        return True
    parts = raw_name.replace("\\", "/").split("/")
    return ".." in parts


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """Unix-режим файла лежит в старших 16 битах external_attr — так
    `zipfile` хранит permission bits ZIP-архивов, созданных на Unix."""
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _normalize_path(raw_name: str) -> str:
    normalized = unicodedata.normalize("NFC", raw_name.replace("\\", "/"))
    return normalized[:512]


def _classify_member(ordinal: int, info: zipfile.ZipInfo) -> MemberDecision:
    raw_name = info.filename
    normalized = _normalize_path(raw_name)
    detected_mime, _ = mimetypes.guess_type(normalized)
    base = dict(ordinal=ordinal, path_original=raw_name, path_normalized=normalized,
               declared_compressed_size=info.compress_size,
               declared_uncompressed_size=info.file_size, detected_mime=detected_mime)

    if _is_unsafe_path(raw_name):
        return MemberDecision(**base, status="quarantine", reason="unsafe path (traversal/absolute)")
    if _is_symlink(info):
        return MemberDecision(**base, status="quarantine", reason="symlink entry")
    if normalized.count("/") + 1 > MAX_PATH_DEPTH:
        return MemberDecision(**base, status="quarantine", reason="path depth exceeds limit")

    ext = Path(normalized).suffix.lower()
    if ext in _NESTED_ARCHIVE_EXTS:
        return MemberDecision(**base, status="skipped_nested_archive")
    if ext in _EXECUTABLE_EXTS:
        return MemberDecision(**base, status="skipped_unsupported", reason="executable format")

    if info.file_size > MAX_MEMBER_UNCOMPRESSED_BYTES:
        return MemberDecision(**base, status="quarantine", reason="member exceeds size limit")
    if info.compress_size > 0:
        ratio = info.file_size / info.compress_size
        if ratio > MAX_COMPRESSION_RATIO:
            return MemberDecision(**base, status="quarantine", reason="compression ratio exceeds limit")

    return MemberDecision(**base, status="eligible")


def preflight(archive_path: Path) -> list[MemberDecision]:
    """Инспекция central directory, БЕЗ распаковки байт.

    Поднимает `ArchiveBlocked` для проблем всего архива: битый ZIP,
    зашифрованные элементы, превышение членов/общего размера. Всё
    остальное — per-member решение (quarantine/skipped_*/eligible),
    один плохой член не блокирует остальных (§14.4.0: "Batch is an
    aggregation, not an all-or-nothing transaction").
    """
    try:
        zf = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise ArchiveBlocked("BLOCKED_INVALID_ZIP", f"не ZIP или повреждён: {exc}") from exc

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and not _is_junk(i.filename)]

        if len(infos) > MAX_MEMBERS:
            raise ArchiveBlocked(
                "BLOCKED_LIMIT",
                f"членов в архиве больше лимита: {len(infos)} > {MAX_MEMBERS}")

        total_uncompressed = sum(i.file_size for i in infos)
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ArchiveBlocked(
                "BLOCKED_LIMIT",
                f"суммарный распакованный размер превышает лимит: "
                f"{total_uncompressed} > {MAX_TOTAL_UNCOMPRESSED_BYTES}")

        for info in infos:
            # §14.7.6: шифрование — решение уровня всего архива ("ask
            # owner for an unencrypted copy"), не per-member quarantine —
            # без пароля нельзя даже понять, что внутри члена.
            if info.flag_bits & 0x1:
                raise ArchiveBlocked("BLOCKED_ENCRYPTED",
                                    "архив содержит зашифрованные элементы")

        return [_classify_member(ordinal, info) for ordinal, info in enumerate(infos, start=1)]


def extract_member(archive_path: Path, decision: MemberDecision, dest_path: Path) -> str:
    """Потоково извлечь ОДИН eligible-член, посчитать SHA256 по факту
    прочитанных байт (не по заявленному в central directory), атомарно
    переложить в `dest_path`. Возвращает hex SHA256.

    CRC проверяет сам `zipfile.ZipExtFile.read()` при дочтении до EOF —
    несовпадение поднимает `zipfile.BadZipFile` тем же путём, что и битый
    архив на preflight. Реальный размер, а не заявленный, — защита от
    архива, чей central directory лжёт о размере члена.
    """
    if not decision.eligible:
        raise ValueError(f"member {decision.path_original!r} не eligible, извлекать нечего")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.parent / f".{dest_path.name}.part-{uuid.uuid4().hex}"
    sha256 = hashlib.sha256()
    written = 0
    try:
        with zipfile.ZipFile(archive_path) as zf, \
             zf.open(decision.path_original) as member, \
             open(tmp_path, "wb") as out:
            while True:
                chunk = member.read(READ_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_MEMBER_UNCOMPRESSED_BYTES:
                    raise ArchiveBlocked(
                        "BLOCKED_LIMIT",
                        f"член {decision.path_original!r} превысил лимит при потоковом чтении")
                sha256.update(chunk)
                out.write(chunk)
        os.replace(tmp_path, dest_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return sha256.hexdigest()
