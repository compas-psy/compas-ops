"""Безопасная распаковка ZIP (v3.7 §14.7.6) — чистые функции, без БД."""

import zipfile

import pytest

from helm_core.knowledge.zip_safety import (
    ArchiveBlocked, extract_member, preflight,
)


def _make_zip(path, entries: dict[str, bytes], *, compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, "w", compression=compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_preflight_normal_zip_all_eligible(tmp_path):
    zpath = _make_zip(tmp_path / "a.zip", {"one.txt": b"hello", "two.txt": b"world"})
    decisions = preflight(zpath)
    assert len(decisions) == 2
    assert all(d.eligible for d in decisions)


def test_preflight_ignores_packaging_junk(tmp_path):
    zpath = _make_zip(tmp_path / "a.zip", {
        "doc.txt": b"real content",
        "__MACOSX/._doc.txt": b"junk",
        ".DS_Store": b"junk",
    })
    decisions = preflight(zpath)
    assert len(decisions) == 1
    assert decisions[0].path_original == "doc.txt"


def test_preflight_rejects_bad_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip file at all")
    with pytest.raises(ArchiveBlocked) as exc:
        preflight(bad)
    assert exc.value.code == "BLOCKED_INVALID_ZIP"


def test_preflight_quarantines_path_traversal(tmp_path):
    zpath = tmp_path / "slip.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("../../etc/passwd", b"pwned")
    decisions = preflight(zpath)
    assert len(decisions) == 1
    assert decisions[0].status == "quarantine"
    assert "unsafe path" in decisions[0].reason


def test_preflight_quarantines_absolute_path(tmp_path):
    zpath = tmp_path / "abs.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("/etc/passwd", b"pwned")
    decisions = preflight(zpath)
    assert decisions[0].status == "quarantine"


def test_preflight_quarantines_symlink(tmp_path):
    zpath = tmp_path / "link.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        info = zipfile.ZipInfo("evil-link")
        info.create_system = 3  # Unix
        info.external_attr = (0o120777 << 16)  # S_IFLNK
        zf.writestr(info, "/etc/passwd")
    decisions = preflight(zpath)
    assert len(decisions) == 1
    assert decisions[0].status == "quarantine"
    assert "symlink" in decisions[0].reason


def test_preflight_blocks_encrypted_archive(tmp_path):
    """`preflight()` решает по general purpose flag bit 0 — том же бите,
    которым реальный encrypted ZIP помечает каждый свой элемент.

    `zipfile.writestr()` всегда пересчитывает `flag_bits` сам и стирает
    любое значение, выставленное на `ZipInfo` заранее — поэтому бит здесь
    патчится напрямую в уже записанных байтах (offset+6 в локальном
    заголовке, offset+8 в central directory — оба по спеке ZIP), не через
    публичный API `zipfile`, у которого просто нет пути создать такой файл."""
    zpath = tmp_path / "enc.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("secret.txt", "top secret")

    data = bytearray(zpath.read_bytes())
    data[data.find(b"PK\x03\x04") + 6] |= 0x1
    data[data.find(b"PK\x01\x02") + 8] |= 0x1
    zpath.write_bytes(bytes(data))

    with pytest.raises(ArchiveBlocked) as exc:
        preflight(zpath)
    assert exc.value.code == "BLOCKED_ENCRYPTED"


def test_preflight_skips_nested_archive(tmp_path):
    inner = tmp_path / "inner.zip"
    _make_zip(inner, {"x.txt": b"x"})
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, arcname="nested.zip")
    decisions = preflight(outer)
    assert len(decisions) == 1
    assert decisions[0].status == "skipped_nested_archive"


def test_preflight_skips_executable(tmp_path):
    zpath = _make_zip(tmp_path / "exe.zip", {"payload.exe": b"MZ\x90\x00fake"})
    decisions = preflight(zpath)
    assert decisions[0].status == "skipped_unsupported"


def test_preflight_blocks_too_many_members(tmp_path, monkeypatch):
    import helm_core.knowledge.zip_safety as zip_safety
    monkeypatch.setattr(zip_safety, "MAX_MEMBERS", 2)
    zpath = _make_zip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(5)})
    with pytest.raises(ArchiveBlocked) as exc:
        preflight(zpath)
    assert exc.value.code == "BLOCKED_LIMIT"


def test_preflight_blocks_total_uncompressed_over_limit(tmp_path, monkeypatch):
    import helm_core.knowledge.zip_safety as zip_safety
    monkeypatch.setattr(zip_safety, "MAX_TOTAL_UNCOMPRESSED_BYTES", 10)
    zpath = _make_zip(tmp_path / "big.zip", {"f.txt": b"x" * 1000})
    with pytest.raises(ArchiveBlocked) as exc:
        preflight(zpath)
    assert exc.value.code == "BLOCKED_LIMIT"


def test_preflight_quarantines_zip_bomb_ratio(tmp_path, monkeypatch):
    import helm_core.knowledge.zip_safety as zip_safety
    monkeypatch.setattr(zip_safety, "MAX_COMPRESSION_RATIO", 5)
    # Крайне сжимаемые данные — реальный ratio деструктивно выше 5.
    zpath = _make_zip(tmp_path / "bomb.zip", {"bomb.bin": b"\x00" * 1_000_000})
    decisions = preflight(zpath)
    assert decisions[0].status == "quarantine"
    assert "ratio" in decisions[0].reason


def test_extract_member_writes_bytes_and_returns_sha256(tmp_path):
    content = b"the actual content of the member"
    zpath = _make_zip(tmp_path / "a.zip", {"doc.txt": content})
    decisions = preflight(zpath)
    dest = tmp_path / "dest" / "doc.txt"

    import hashlib
    sha = extract_member(zpath, decisions[0], dest)

    assert dest.read_bytes() == content
    assert sha == hashlib.sha256(content).hexdigest()


def test_extract_member_enforces_own_limit_independently_of_preflight(tmp_path, monkeypatch):
    """extract_member() не полагается на то, что preflight() уже
    классифицировал член как безопасный по размеру — считает реальные
    прочитанные байты сам и обрывается сам (§14.7.6: "Declared ZIP
    metadata is not trusted" — тот же принцип применён и здесь: не
    доверять решению, принятому раньше на других данных)."""
    import helm_core.knowledge.zip_safety as zip_safety
    zpath = _make_zip(tmp_path / "a.zip", {"doc.txt": b"x" * 1000})
    decisions = preflight(zpath)  # лимит ещё дефолтный — member eligible
    assert decisions[0].eligible

    monkeypatch.setattr(zip_safety, "MAX_MEMBER_UNCOMPRESSED_BYTES", 100)
    dest = tmp_path / "dest" / "doc.txt"

    with pytest.raises(ArchiveBlocked) as exc:
        extract_member(zpath, decisions[0], dest)
    assert exc.value.code == "BLOCKED_LIMIT"
    assert not dest.exists()
