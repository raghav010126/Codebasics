"""Normalise user text before rule matching so obfuscation cannot hide an attack.

NFKC folding, casefolding, invisible/bidi character detection, Cyrillic/Greek homoglyph folding, and decoding of
base64/hex runs (decoded text is scanned with the same rules).
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field

INVISIBLE = re.compile(r"[\u200b-\u200f\u2060\ufeff\u202a-\u202e\u2066-\u2069\u00ad]")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
    "ο": "o", "α": "a", "ε": "e", "ι": "i", "ν": "v", "Ο": "O", "Α": "A", "Ε": "E",
})
_B64 = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{20,}={0,2}(?![A-Za-z0-9+/=])")
_HEX = re.compile(r"\b(?:[0-9a-fA-F]{2}){12,}\b")


@dataclass
class Normalized:
    original: str
    text: str                                  # folded, casefolded, invisible characters removed
    flags: list[str] = field(default_factory=list)
    decoded: list[str] = field(default_factory=list)

    @property
    def scan_texts(self) -> list[str]:
        return [self.text, *self.decoded]


def _printable_words(raw: bytes) -> str | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text or sum(ch.isprintable() or ch.isspace() for ch in text) / len(text) < 0.95:
        return None
    return text if len(re.findall(r"[A-Za-z]{2,}", text)) >= 3 else None


def normalize(text: str) -> Normalized:
    flags = []
    if INVISIBLE.search(text):
        flags.append("invisible_chars")
    if CONTROL.search(text):
        flags.append("control_chars")
    folded = unicodedata.normalize("NFKC", text)
    if folded.translate(_HOMOGLYPHS) != folded:
        flags.append("homoglyphs")
    folded = INVISIBLE.sub("", folded.translate(_HOMOGLYPHS))
    folded = CONTROL.sub(" ", folded)
    norm = re.sub(r"\s+", " ", folded).strip().casefold()
    decoded = []
    for m in _B64.finditer(folded):
        blob = m.group(0)
        try:
            words = _printable_words(base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True))
        except (binascii.Error, ValueError):
            words = None
        if words:
            decoded.append(re.sub(r"\s+", " ", words).casefold())
    for m in _HEX.finditer(folded):
        words = _printable_words(bytes.fromhex(m.group(0)))
        if words:
            decoded.append(re.sub(r"\s+", " ", words).casefold())
    if decoded:
        flags.append("encoded_payload")
    return Normalized(original=text, text=norm, flags=flags, decoded=decoded)
