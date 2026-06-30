"""Phase 0 contract tests.

These also serve as the WORKED EXAMPLE of the test style every later phase must
match: each test name reads as a policy scenario, and each assertion encodes a
rule a domain expert would recognize — not 'the function returns something'.

Run: pytest -q
"""

from datetime import date

import pytest
from pydantic import ValidationError

from policyforge.schemas import (
    Claim,
    ClaimLine,
    DispositionStatus,
    ModifierIndicator,
    PTPRule,
    RuleCandidate,
)


# --- PTPRule: the authoritative rule shape ---------------------------------- #
def test_active_edit_governs_a_service_inside_its_date_window():
    rule = PTPRule(
        column_1="11042",
        column_2="97597",
        modifier_indicator=ModifierIndicator.ALLOWED,
        effective_date=date(1996, 1, 1),
        rationale="Standards of medical / surgical practice",
    )
    assert rule.is_active_on(date(2026, 7, 1)) is True


def test_deleted_edit_does_not_govern_a_service_after_its_deletion():
    rule = PTPRule(
        column_1="36415",
        column_2="36416",
        modifier_indicator=ModifierIndicator.NOT_APPLICABLE,
        effective_date=date(2002, 1, 1),
        deletion_date=date(2023, 12, 31),
        rationale="Mutually exclusive procedures",
    )
    assert rule.is_active_on(date(2026, 7, 1)) is False


def test_an_edit_cannot_pair_a_code_with_itself():
    with pytest.raises(ValidationError):
        PTPRule(
            column_1="93000",
            column_2="93000",
            modifier_indicator=ModifierIndicator.NOT_ALLOWED,
            effective_date=date(2000, 1, 1),
            rationale="invalid self-pair",
        )


def test_rule_id_is_stable_provenance_for_a_code_pair():
    rule = PTPRule(
        column_1="93000",
        column_2="93005",
        modifier_indicator=ModifierIndicator.NOT_ALLOWED,
        effective_date=date(2000, 1, 1),
        rationale="Misuse of column two code with column one code",
    )
    assert rule.rule_id == "PTP:93000:93005"


def test_modifier_allowed_edit_compiles_to_conditional_json_logic():
    # A CCMI=1 edit must compile to a rule whose denial is CONDITIONAL on a
    # bypass modifier; a CCMI=0 edit must compile to an UNCONDITIONAL denial.
    allowed = PTPRule(
        column_1="11042", column_2="97597",
        modifier_indicator=ModifierIndicator.ALLOWED,
        effective_date=date(1996, 1, 1), rationale="x",
    ).to_json_logic()
    not_allowed = PTPRule(
        column_1="93000", column_2="93005",
        modifier_indicator=ModifierIndicator.NOT_ALLOWED,
        effective_date=date(2000, 1, 1), rationale="x",
    ).to_json_logic()
    assert isinstance(allowed["then_column_2"], dict)        # conditional
    assert not_allowed["then_column_2"] == DispositionStatus.DENY.value  # unconditional


# --- Claim: the engine input ------------------------------------------------ #
def test_a_claim_rejects_duplicate_line_ids():
    with pytest.raises(ValidationError):
        Claim(
            claim_id="C1", beneficiary_id="B1", provider_id="P1",
            lines=[
                ClaimLine(line_id="L1", code="11042", date_of_service=date(2026, 7, 1)),
                ClaimLine(line_id="L1", code="97597", date_of_service=date(2026, 7, 1)),
            ],
        )


def test_a_claim_line_rejects_zero_units():
    with pytest.raises(ValidationError):
        ClaimLine(line_id="L1", code="11042", units=0, date_of_service=date(2026, 7, 1))


# --- RuleCandidate: the LLM output, pre-gate -------------------------------- #
def test_a_candidate_matches_an_authoritative_rule_on_pair_and_indicator_only():
    rule = PTPRule(
        column_1="11042", column_2="97597",
        modifier_indicator=ModifierIndicator.ALLOWED,
        effective_date=date(1996, 1, 1), rationale="authoritative wording",
    )
    candidate = RuleCandidate(
        column_1="11042", column_2="97597",
        modifier_indicator=ModifierIndicator.ALLOWED,
        rationale="completely different wording from the model",
        source_chapter="Chapter 1", source_quote="...", extraction_confidence=0.8,
    )
    # Same pair + indicator => true positive, regardless of rationale wording.
    assert candidate.matches(rule) is True


def test_a_candidate_with_wrong_modifier_indicator_does_not_match():
    rule = PTPRule(
        column_1="11042", column_2="97597",
        modifier_indicator=ModifierIndicator.ALLOWED,
        effective_date=date(1996, 1, 1), rationale="x",
    )
    candidate = RuleCandidate(
        column_1="11042", column_2="97597",
        modifier_indicator=ModifierIndicator.NOT_ALLOWED,  # wrong CCMI
        rationale="x", source_chapter="Chapter 1", source_quote="...",
        extraction_confidence=0.9,
    )
    assert candidate.matches(rule) is False


def test_confidence_outside_zero_to_one_is_rejected():
    with pytest.raises(ValidationError):
        RuleCandidate(
            column_1="11042", column_2="97597",
            modifier_indicator=ModifierIndicator.ALLOWED, rationale="x",
            source_chapter="Chapter 1", source_quote="...", extraction_confidence=1.4,
        )
