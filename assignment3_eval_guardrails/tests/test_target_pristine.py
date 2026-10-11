"""MediBot under target/medibot must stay byte-identical to the submitted commit (see target/PROVENANCE.md)."""

from __future__ import annotations

import hashlib
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "target"
IGNORED_PARTS = {"__pycache__", ".pytest_cache", ".DS_Store"}


def _manifest() -> dict[str, str]:
    out = {}
    for line in (TARGET / "medibot.sha256").read_text().splitlines():
        digest, path = line.split(maxsplit=1)
        out[path.removeprefix("./")] = digest
    return out


def _current() -> dict[str, str]:
    root = TARGET / "medibot"
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not IGNORED_PARTS & set(rel.parts):
            out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_medibot_copy_is_unmodified():
    expected, actual = _manifest(), _current()
    assert sorted(set(actual) - set(expected)) == [], "files added to target/medibot"
    assert sorted(set(expected) - set(actual)) == [], "files removed from target/medibot"
    changed = sorted(p for p in expected if expected[p] != actual[p])
    assert changed == [], f"files changed in target/medibot: {changed}"
