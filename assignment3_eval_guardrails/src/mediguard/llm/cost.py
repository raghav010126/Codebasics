"""Per-model token prices (USD per million tokens) and cost of one call."""

from __future__ import annotations

PRICES = {  # input, output; cache reads bill at 10% of input
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0) -> float:
    key = next((k for k in PRICES if model.startswith(k)), None)
    if key is None:
        return 0.0
    pin, pout = PRICES[key]
    return round((input_tokens * pin + cache_read_tokens * pin * 0.1 + output_tokens * pout) / 1e6, 6)
