"""The contract any chatbot implements to be wrapped by the guardrail and evaluation layer."""

from __future__ import annotations

from typing import Protocol

from mediguard.models import Scope, TargetResponse


class TargetAdapter(Protocol):
    name: str
    version: str
    model: str

    def respond(self, question: str, role: str) -> TargetResponse: ...

    def scope(self, role: str) -> Scope: ...
