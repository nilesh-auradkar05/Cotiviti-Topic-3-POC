"""Phase 4 deterministic engine tests."""

from __future__ import annotations

from datetime import date

from policyforge.engine import NCCI_PTP_ASSOCIATED_MODIFIERS, adjudicate
from policyforge.schemas import (
    Claim,
    ClaimLine,
    DispositionStatus,
    ModifierIndicator,
    PTPRule,
)


RULESET_VERSION = "ncci-2026-q3"
SERVICE_DATE = date(2026, 7, 1)


def _rule(
    column_1: str,
    column_2: str,
    modifier_indicator: ModifierIndicator,
    *,
    effective_date: date = date(2020, 1, 1),
    deletion_date: date | None = None,
) -> PTPRule:
    return PTPRule(
        column_1=column_1,
        column_2=column_2,
        modifier_indicator=modifier_indicator,
        effective_date=effective_date,
        deletion_date=deletion_date,
        rationale="test PTP edit",
    )


def _line(
    line_id: str,
    code: str,
    *,
    modifiers: list[str] | None = None,
    dos: date = SERVICE_DATE,
) -> ClaimLine:
    return ClaimLine(
        line_id=line_id,
        code=code,
        modifiers=[] if modifiers is None else modifiers,
        date_of_service=dos,
    )


def _claim(*lines: ClaimLine) -> Claim:
    return Claim(
        claim_id="C1",
        beneficiary_id="B1",
        provider_id="P1",
        lines=list(lines),
    )


def _by_line(disposition):
    return {line.line_id: line for line in disposition.line_dispositions}


def _decision_tuples(disposition):
    return [
        (line.line_id, line.status, line.reason_code, line.cited_rule_id)
        for line in disposition.line_dispositions
    ]


def test_adjudication_is_deterministic_and_preserves_claim_line_order():
    claim = _claim(
        _line("L3", "80053"),
        _line("L1", "93000"),
        _line("L2", "93005"),
    )
    rules = [_rule("93000", "93005", ModifierIndicator.NOT_ALLOWED)]

    first = adjudicate(claim, rules, RULESET_VERSION)
    second = adjudicate(claim, rules, RULESET_VERSION)

    assert _decision_tuples(first) == _decision_tuples(second)
    assert [line.line_id for line in first.line_dispositions] == ["L3", "L1", "L2"]
    assert _decision_tuples(first) == [
        ("L3", DispositionStatus.PAY, None, None),
        ("L1", DispositionStatus.PAY, None, None),
        ("L2", DispositionStatus.DENY, "CO-97", "PTP:93000:93005"),
    ]


def test_same_severity_edits_cite_the_smaller_rule_id_independent_of_rule_order():
    claim = _claim(
        _line("L1", "11042"),
        _line("L2", "36415"),
        _line("L3", "97597"),
    )
    higher_rule = _rule("36415", "97597", ModifierIndicator.NOT_ALLOWED)
    lower_rule = _rule("11042", "97597", ModifierIndicator.NOT_ALLOWED)

    first = adjudicate(claim, [higher_rule, lower_rule], RULESET_VERSION)
    second = adjudicate(claim, [lower_rule, higher_rule], RULESET_VERSION)

    assert _by_line(first)["L3"].status is DispositionStatus.DENY
    assert _by_line(first)["L3"].cited_rule_id == "PTP:11042:97597"
    assert _decision_tuples(first) == _decision_tuples(second)


def test_a_ccmi_zero_pair_denies_the_column_two_line_with_co_97():
    disposition = adjudicate(
        _claim(_line("L1", "93000"), _line("L2", "93005")),
        [_rule("93000", "93005", ModifierIndicator.NOT_ALLOWED)],
        RULESET_VERSION,
    )

    lines = _by_line(disposition)
    assert lines["L1"].status is DispositionStatus.PAY
    assert lines["L1"].cited_rule_id is None
    assert lines["L2"].status is DispositionStatus.DENY
    assert lines["L2"].reason_code == "CO-97"
    assert lines["L2"].cited_rule_id == "PTP:93000:93005"
    assert disposition.ruleset_version == RULESET_VERSION


def test_a_ccmi_one_pair_with_a_bypass_modifier_flags_the_column_two_line():
    disposition = adjudicate(
        _claim(_line("L1", "11042"), _line("L2", "97597", modifiers=["59"])),
        [_rule("11042", "97597", ModifierIndicator.ALLOWED)],
        RULESET_VERSION,
    )

    line = _by_line(disposition)["L2"]
    assert line.status is DispositionStatus.FLAG
    assert line.reason_code is None
    assert line.cited_rule_id == "PTP:11042:97597"
    assert "59" in line.explanation


def test_a_ccmi_one_pair_without_a_bypass_modifier_denies_the_column_two_line():
    disposition = adjudicate(
        _claim(_line("L1", "11042"), _line("L2", "97597")),
        [_rule("11042", "97597", ModifierIndicator.ALLOWED)],
        RULESET_VERSION,
    )

    line = _by_line(disposition)["L2"]
    assert line.status is DispositionStatus.DENY
    assert line.reason_code == "CO-97"
    assert line.cited_rule_id == "PTP:11042:97597"


def test_a_ccmi_nine_edit_takes_no_action():
    disposition = adjudicate(
        _claim(_line("L1", "36415"), _line("L2", "36416")),
        [_rule("36415", "36416", ModifierIndicator.NOT_APPLICABLE)],
        RULESET_VERSION,
    )

    assert {line.status for line in disposition.line_dispositions} == {DispositionStatus.PAY}
    assert all(line.cited_rule_id is None for line in disposition.line_dispositions)


def test_an_edit_outside_its_date_window_does_not_fire():
    disposition = adjudicate(
        _claim(_line("L1", "36415"), _line("L2", "36416")),
        [
            _rule(
                "36415",
                "36416",
                ModifierIndicator.NOT_ALLOWED,
                effective_date=date(2002, 1, 1),
                deletion_date=date(2023, 12, 31),
            )
        ],
        RULESET_VERSION,
    )

    assert {line.status for line in disposition.line_dispositions} == {DispositionStatus.PAY}


def test_a_pair_the_ruleset_never_mentions_pays():
    disposition = adjudicate(
        _claim(_line("L1", "99213"), _line("L2", "80053")),
        [_rule("93000", "93005", ModifierIndicator.NOT_ALLOWED)],
        RULESET_VERSION,
    )

    assert {line.status for line in disposition.line_dispositions} == {DispositionStatus.PAY}


def test_column_order_matters_for_ptp_edits():
    disposition = adjudicate(
        _claim(_line("L1", "97597"), _line("L2", "11042")),
        [_rule("97597", "11042", ModifierIndicator.NOT_ALLOWED)],
        RULESET_VERSION,
    )

    lines = _by_line(disposition)
    assert lines["L1"].status is DispositionStatus.PAY
    assert lines["L2"].status is DispositionStatus.DENY
    assert lines["L2"].cited_rule_id == "PTP:97597:11042"


def test_same_date_of_service_is_required_for_a_ptp_pair():
    disposition = adjudicate(
        _claim(
            _line("L1", "93000", dos=date(2026, 7, 1)),
            _line("L2", "93005", dos=date(2026, 7, 2)),
        ),
        [_rule("93000", "93005", ModifierIndicator.NOT_ALLOWED)],
        RULESET_VERSION,
    )

    assert {line.status for line in disposition.line_dispositions} == {DispositionStatus.PAY}


def test_the_bypass_modifier_must_be_on_the_column_two_line():
    disposition = adjudicate(
        _claim(_line("L1", "11042", modifiers=["59"]), _line("L2", "97597")),
        [_rule("11042", "97597", ModifierIndicator.ALLOWED)],
        RULESET_VERSION,
    )

    line = _by_line(disposition)["L2"]
    assert line.status is DispositionStatus.DENY
    assert line.reason_code == "CO-97"


def test_most_severe_disposition_wins_across_edits():
    disposition = adjudicate(
        _claim(
            _line("L1", "11042"),
            _line("L2", "93000"),
            _line("L3", "97597", modifiers=["59"]),
        ),
        [
            _rule("11042", "97597", ModifierIndicator.ALLOWED),
            _rule("93000", "97597", ModifierIndicator.NOT_ALLOWED),
        ],
        RULESET_VERSION,
    )

    line = _by_line(disposition)["L3"]
    assert line.status is DispositionStatus.DENY
    assert line.reason_code == "CO-97"
    assert line.cited_rule_id == "PTP:93000:97597"


def test_every_line_gets_a_pay_disposition_when_no_edit_fires():
    disposition = adjudicate(
        _claim(_line("L1", "99213"), _line("L2", "80053"), _line("L3", "36415")),
        [],
        RULESET_VERSION,
    )

    assert len(disposition.line_dispositions) == 3
    assert {line.status for line in disposition.line_dispositions} == {DispositionStatus.PAY}
    assert all(line.reason_code is None for line in disposition.line_dispositions)
    assert all(line.cited_rule_id is None for line in disposition.line_dispositions)


def test_every_non_pay_line_cites_a_rule_and_the_ruleset_version():
    disposition = adjudicate(
        _claim(_line("L1", "11042"), _line("L2", "97597", modifiers=["XS"])),
        [_rule("11042", "97597", ModifierIndicator.ALLOWED)],
        RULESET_VERSION,
    )

    non_pay = [line for line in disposition.line_dispositions if line.status is not DispositionStatus.PAY]
    assert non_pay
    assert all(line.cited_rule_id for line in non_pay)
    assert disposition.ruleset_version == RULESET_VERSION


def test_the_ncci_ptp_associated_modifier_set_is_pinned():
    assert NCCI_PTP_ASSOCIATED_MODIFIERS == {
        "E1", "E2", "E3", "E4",
        "FA", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9",
        "TA", "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9",
        "LT", "RT", "LC", "LD", "RC", "LM", "RI",
        "24", "25", "57", "58", "78", "79",
        "27", "59", "91",
        "XE", "XP", "XS", "XU",
    }
