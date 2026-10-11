"""The only messages a user sees when the layer stops a request. They never contain the reason."""

from __future__ import annotations

REFUSAL = ("I can't help with that request. If you think this is a mistake, contact the IT service desk and quote "
           "reference {request_id}.")
UNAVAILABLE = "I couldn't complete that request right now. Please try again later (reference {request_id})."


def refusal(request_id: str) -> str:
    return REFUSAL.format(request_id=request_id)


def unavailable(request_id: str) -> str:
    return UNAVAILABLE.format(request_id=request_id)


def strip_reference(text: str) -> str:
    """Refusal text with the request id removed (for checking that every refusal is identical)."""
    return text.rsplit("reference ", 1)[0]
