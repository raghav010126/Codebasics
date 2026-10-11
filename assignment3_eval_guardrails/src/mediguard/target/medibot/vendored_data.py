"""Read the datasets defined in MediBot's scripts/ without importing them (they write into MediBot's tree on import).

Usage: from mediguard.target.medibot.vendored_data import markers, attacks
"""

from __future__ import annotations

import ast
from functools import lru_cache
from typing import Any

from mediguard.config import get_settings


@lru_cache
def _literals(script: str) -> dict[str, Any]:
    tree = ast.parse((get_settings().medibot_dir / "scripts" / script).read_text())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return out


def markers() -> dict[str, list[str]]:
    """Strings that occur only inside one restricted collection (MediBot's own red-team markers)."""
    return _literals("redteam.py")["MARKERS"]


def attacks() -> list[tuple[str, str, str]]:
    return _literals("redteam.py")["ATTACKS"]


def sql_attacks() -> list[tuple[str, str]]:
    return _literals("redteam.py")["SQL_ATTACKS"]


def retrieval_queries() -> list[tuple[str, str, str, str]]:
    return _literals("eval_retrieval.py")["QUERIES"]


def sql_cases() -> list[tuple]:
    return _literals("sql_benchmark.py")["CASES"]


def calibration_questions() -> tuple[list[str], list[str]]:
    lit = _literals("calibrate_threshold.py")
    return lit["IN"], lit["OUT"]
