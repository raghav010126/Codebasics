"""Doses, codes and amounts must come from the passages; doses must sit next to the right drug."""

from __future__ import annotations

from conftest import needs_index

from mediguard.guardrails.checks.numeric_grounding import extract_claims, ground_answer

VANCO = ("Source: drug_formulary.pdf - 1. Antimicrobials\n| Vancomycin | Glycopeptide | IV | 15-20 mg/kg Q12H | 3 | "
         "Room temp | TDM; trough 15-20 mg/L |\n| Glipizide | Sulfonylurea | Oral | 5 mg OD | 1 | Room temp | |")
RATES = "| I21.0 | STEMI - anterior wall | 5-7 days | ₹1,85,000 | Yes |\n| I21.4 | NSTEMI | 3-5 days | ₹1,20,000 | Yes |"


def _bad(answer, contexts, q="", rows=None):
    return [(c.kind, c.raw) for c in ground_answer(answer, contexts, q, rows).ungrounded]


def test_extracts_kinds():
    kinds = {c.kind for c in extract_claims("Give 15-20 mg/kg [1], costs ₹1,20,000, 7.5% and 38.5 °C; code I21.4, F-12")}
    assert kinds == {"dose", "money", "percent", "temperature", "code"}


@needs_index
def test_grounded_answer_passes():
    assert _bad("- **Standard dose:** 15-20 mg/kg Q12H [1]\n- trough 15–20 mg/L [1]", [VANCO], "vancomycin dose?") == []


@needs_index
def test_wrong_dose_flagged():
    assert _bad("Vancomycin 25-30 mg/kg IV Q6H [1]", [VANCO]) == [("dose", "25-30 mg/kg")]


@needs_index
def test_dose_of_another_drug_flagged():
    assert _bad("Warfarin starting dose is 5 mg OD [1].", [VANCO], "warfarin dose?") == [("dose", "5 mg")]


def test_money_indian_grouping_and_codes():
    assert _bad("I21.4 is ₹1,20,000 (1.2 lakh) [1].", [RATES]) == []
    assert _bad("I21.0 is ₹2,40,000 [1].", [RATES]) == [("money", "₹2,40,000")]
    assert _bad("Use code I99.9 [1].", [RATES]) == [("code", "I99.9")]


def test_sql_rows_ground_values():
    assert _bad("8 escalated claims totalling ₹14,74,700.", ["SQL used: ..."], rows=[[8, 1474700.0]]) == []


@needs_index
def test_label_words_are_not_drug_anchors():
    ans = "- **Standard dose:** 15-20 mg/kg Q12H [1]\n- **Monitoring:** TDM; trough 15-20 mg/L [1]"
    assert _bad(ans, [VANCO], "Vancomycin dose") == []


@needs_index
def test_unknown_drug_written_before_dose_must_be_in_passage():
    assert _bad("Oxycodone 5 mg OD [1].", [VANCO], "pain relief dose?") == [("dose", "5 mg")]
