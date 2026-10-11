"""Personal identifiers are hashed before they reach traces or logs; the hospital's own contacts stay readable."""

from __future__ import annotations

import pytest

from mediguard.observability.scrub import scrub, scrub_text


@pytest.mark.parametrize("text, raw, tag", [
    ("My Aadhaar is 2345 6789 0123, update payroll", "2345 6789 0123", "<AADHAAR#"),
    ("and my PAN is ABCDE1234F", "ABCDE1234F", "<PAN#"),
    ("card 4111 1111 1111 1111 on file", "4111 1111 1111 1111", "<CARD#"),
    ("call me on +91 98765 43210", "98765 43210", "<PHONE#"),
    ("mail raj.k@gmail.com today", "raj.k@gmail.com", "<EMAIL#"),
    ("claim CLM-2024-0012 for PAT-10042", "PAT-10042", "<ID#"),
])
def test_personal_identifiers_are_hashed(text, raw, tag):
    out = scrub_text(text)
    assert raw not in out and tag in out


@pytest.mark.parametrize("text", [
    "HR Helpdesk: hr.help@mediassist.in, 1800-425-3333",
    "https://smith.langchain.com/o/1a2b/projects/p/9c0d/r/0f8e7d6c-1234-5678-9abc-234567890123?poll=true",
    "4111 1111 1111 1112",
    "claimed 45000 on 2026-10-10T12:58:14",
])
def test_public_contacts_trace_ids_and_plain_numbers_untouched(text):
    assert scrub_text(text) == text


def test_tags_are_stable_across_formats_and_nested_values():
    assert scrub_text("2345 6789 0123") == scrub_text("234567890123")
    assert scrub_text("PAN abcde1234f") == scrub_text("PAN ABCDE1234F")
    out = scrub({"q": ["PAN abcde1234f"], "n": 3})
    assert out["n"] == 3 and "abcde1234f" not in out["q"][0]
