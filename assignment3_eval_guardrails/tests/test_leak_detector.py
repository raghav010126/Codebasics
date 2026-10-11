"""Restricted content never reaches a role that cannot read it; legitimate content passes."""

from __future__ import annotations

import pytest
from conftest import ctx, needs_index

from mediguard.guardrails.checks.leak_detector import LeakDetector
from mediguard.guardrails.checks.rules_output import OutputRules, luhn_ok
from mediguard.models import TargetResponse

pytestmark = needs_index


@pytest.mark.parametrize("role, answer, reason", [
    ("nurse", "The J18.9 package rate is ₹45,000 per BILL-CODE-010.", "output.leak.marker"),
    ("technician", "Niva Bupa covers this admission.", "output.leak.marker"),
    ("doctor", "Kavya Pillai's claim CLM-2024-1000 is pending.", "output.leak.patient_data"),
    ("billing_executive", "Ticket TKT-2024-2001 was raised by Riya Nair.", "output.leak.ticket_data"),
])
def test_leaks_block(role, answer, reason):
    v = LeakDetector().evaluate(ctx("question", role, answer))
    assert v.decision == "block" and v.reason_code == reason


def test_unreadable_source_blocks():
    t = TargetResponse(status="ok", answer="ok [1]", sources=[{"source_document": "drug_formulary.pdf",
                                                              "collection": "clinical"}])
    assert LeakDetector().evaluate(ctx("q", "technician", "ok [1]", t)).reason_code == "output.leak.source_collection"


@pytest.mark.parametrize("role, answer", [
    ("nurse", "Contact the HR Helpdesk on 1800-180-4500 or hr.help@mediassist.in [1]."),
    ("doctor", "Vancomycin is 15-20 mg/kg IV Q12H; trough 15-20 mg/L [1]."),
    ("billing_executive", "I21.4 (NSTEMI) has a package rate of ₹1,20,000 and needs pre-authorisation [1]."),
    ("admin", "Kavya Pillai's claim is pending."),
])
def test_legitimate_answers_pass(role, answer):
    assert LeakDetector().evaluate(ctx("q", role, answer)).decision == "allow"


def test_output_rules():
    r = OutputRules()
    assert r.evaluate(ctx("q", "nurse", "My Aadhaar is 2345 6789 0123")).reason_code == "output.rules.pii_aadhaar"
    assert r.evaluate(ctx("q", "nurse", "Blocked by the guardrail.")).reason_code == "output.rules.internal_terms"
    assert r.evaluate(ctx("q", "nurse", "Change the dressing every 72 hours [1].")).decision == "allow"
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112")
