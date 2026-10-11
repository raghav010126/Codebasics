"""Load config/rules.yaml once and compile its patterns."""

from __future__ import annotations

import re
from functools import lru_cache

import yaml

from mediguard.config import get_settings


@lru_cache
def rules() -> dict:
    return yaml.safe_load((get_settings().project_root / "config" / "rules.yaml").read_text())


@lru_cache
def compiled(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


def luhn_ok(digits: str) -> bool:
    nums = [int(d) for d in digits][::-1]
    total = sum(n if i % 2 == 0 else (n * 2 - 9 if n * 2 > 9 else n * 2) for i, n in enumerate(nums))
    return total % 10 == 0
