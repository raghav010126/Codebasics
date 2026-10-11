"""Create or update the Bedrock guardrail defined in config/guardrail_policy.yaml (same as `mediguard guardrail-setup`).

Usage: uv run python scripts/create_bedrock_guardrail.py [--draft]
"""

from __future__ import annotations

import json
import sys

from mediguard.guardrails.bedrock_setup import create_or_update

if __name__ == "__main__":
    print(json.dumps(create_or_update(publish="--draft" not in sys.argv), indent=1))
