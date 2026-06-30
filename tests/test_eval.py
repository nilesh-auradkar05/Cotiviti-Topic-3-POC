"""Phase 5 evaluation tests."""

from __future__ import annotations

import builtins
from datetime import date

from policyforge.evaluation import evaluate, score_track_a, score_track_b
from policyforge.evaluation.run_eval import (
    _claim_cases_from_gold_rows,
    _rules_from_gold_rows,
    _status_for_expected_decision,
)
from policyforge.retriever import PolicyChunk
from policyforge.schemas import (
    Claim,
    ClaimLine,
    DispositionStatus,
    ModifierIndicator,
    PTPRule,
    RuleCandidate,
)


RULESET_VERSION = "ncci-2026-q3"
SERVICE_DATE = date(2026, 7, 15)


class _FakeRetriever:
    def __init__(self, name: str, chunks: list[PolicyChunk]) -> None:
        self.name = name
        self._chunks = chunks

    def retrieve(self, query: str, k: int = 5) -> list[PolicyChunk]:
        return self._chunks[:k]


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


def _candidate(
    column_1: str,
    column_2: str,
    modifier_indicator: ModifierIndicator,
    *,
    source_chapter: str = "Chapter 1",
) -> RuleCandidate:
    return RuleCandidate(
        column_1=column_1,
        column_2=column_2,
        modifier_indicator=modifier_indicator,
        rationale="candidate rationale",
        source_chapter=source_chapter,
        source_quote=f"{column_1} and {column_2}",
        extraction_confidence=0.9,
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


def _case(
    claim: Claim,
    expected: dict[str, DispositionStatus],
) -> tuple[Claim, dict[str, DispositionStatus]]:
    return claim, expected


def _gold_row(
    *,
    expected_decision: str,
    column_1: str = "93000",
    column_2: str = "93005",
    modifier_indicator: str = "0",
    member_id_col1: str = "M1",
    member_id_col2: str = "M1",
    date_of_service_col1: str = "2026-07-15",
    date_of_service_col2: str = "2026-07-15",
    line2_modifiers: str = "",
    is_source_ptp_pair: str = "1",
) -> dict[str, str]:
    return {
        "is_source_ptp_pair": is_source_ptp_pair,
        "source_column_1": column_1,
        "source_column_2": column_2,
        "source_effective_date": "2020-01-01",
        "source_deletion_date": "",
        "modifier_indicator": modifier_indicator,
        "ptp_edit_rationale": "test PTP edit",
        "claim_id": "C1",
        "member_id_col1": member_id_col1,
        "member_id_col2": member_id_col2,
        "provider_id": "P1",
        "date_of_service_col1": date_of_service_col1,
        "date_of_service_col2": date_of_service_col2,
        "line1_code": column_1,
        "line2_code": column_2,
        "line2_modifiers": line2_modifiers,
        "expected_decision": expected_decision,
    }


def test_track_a_perfect_extraction_scores_every_gold_pair():
    gold = [
        _rule("11042", "97597", ModifierIndicator.ALLOWED),
        _rule("93000", "93005", ModifierIndicator.NOT_ALLOWED),
    ]
    candidates = [
        _candidate("11042", "97597", ModifierIndicator.ALLOWED),
        _candidate("93000", "93005", ModifierIndicator.NOT_ALLOWED),
    ]

    result = score_track_a(candidates, gold, "direct")

    assert result.retriever_name == "direct"
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.f1 == 1.0
    assert result.true_positives == 2
    assert result.false_positives == 0
    assert result.false_negatives == 0
    assert result.n_examples == 2


def test_track_a_matches_one_gold_rule_to_only_one_identical_candidate():
    gold = [_rule("11042", "97597", ModifierIndicator.ALLOWED)]
    candidates = [
        _candidate("11042", "97597", ModifierIndicator.ALLOWED),
        _candidate("11042", "97597", ModifierIndicator.ALLOWED),
    ]

    result = score_track_a(candidates, gold, "direct")

    assert result.true_positives == 1
    assert result.false_positives == 1
    assert result.false_negatives == 0
    assert result.recall == 1.0
    assert result.precision == 0.5


def test_a_corrupted_extraction_lowers_the_track_a_score():
    gold = [
        _rule("11042", "97597", ModifierIndicator.ALLOWED),
        _rule("93000", "93005", ModifierIndicator.NOT_ALLOWED),
    ]
    clean = [
        _candidate("11042", "97597", ModifierIndicator.ALLOWED),
        _candidate("93000", "93005", ModifierIndicator.NOT_ALLOWED),
    ]
    corrupted = [
        _candidate("11042", "97597", ModifierIndicator.NOT_ALLOWED),
        _candidate("93000", "93005", ModifierIndicator.NOT_ALLOWED),
    ]

    clean_score = score_track_a(clean, gold, "direct")
    corrupted_score = score_track_a(corrupted, gold, "direct")

    assert corrupted_score.precision < clean_score.precision
    assert corrupted_score.recall < clean_score.recall
    assert corrupted_score.f1 < clean_score.f1


def test_a_hallucinated_pair_is_a_track_a_false_positive():
    gold = [_rule("11042", "97597", ModifierIndicator.ALLOWED)]
    candidates = [
        _candidate("11042", "97597", ModifierIndicator.ALLOWED),
        _candidate("80053", "36415", ModifierIndicator.NOT_ALLOWED),
    ]

    result = score_track_a(candidates, gold, "direct")

    assert result.false_positives == 1
    assert result.precision < 1.0
    assert result.recall == 1.0


def test_a_missed_pair_is_a_track_a_false_negative():
    gold = [
        _rule("11042", "97597", ModifierIndicator.ALLOWED),
        _rule("93000", "93005", ModifierIndicator.NOT_ALLOWED),
    ]
    candidates = [_candidate("11042", "97597", ModifierIndicator.ALLOWED)]

    result = score_track_a(candidates, gold, "direct")

    assert result.false_negatives == 1
    assert result.precision == 1.0
    assert result.recall < 1.0


def test_zero_track_a_predictions_score_zero_without_crashing():
    result = score_track_a(
        [],
        [_rule("11042", "97597", ModifierIndicator.ALLOWED)],
        "direct",
    )

    assert result.precision == 0.0
    assert result.recall == 0.0
    assert result.f1 == 0.0


def test_both_retriever_arms_are_reported_by_evaluate():
    gold = [_rule("11042", "97597", ModifierIndicator.ALLOWED)]
    claim_cases = [
        _case(
            _claim(_line("L1", "11042"), _line("L2", "97597", modifiers=["59"])),
            {"L1": DispositionStatus.PAY, "L2": DispositionStatus.FLAG},
        )
    ]
    retrievers = [
        _FakeRetriever(
            "direct",
            [PolicyChunk(chapter="Chapter 1", text="Chapter text names 11042 and 97597.")],
        ),
        _FakeRetriever(
            "chroma",
            [PolicyChunk(chapter="Chapter 1", text="A chunk names 11042 and 97597.")],
        ),
    ]

    def extract_fn(text: str, source_chapter: str) -> list[RuleCandidate]:
        if "11042" in text and "97597" in text:
            return [_candidate("11042", "97597", ModifierIndicator.ALLOWED, source_chapter=source_chapter)]
        return []

    report = evaluate(
        rules=gold,
        gold_examples=gold,
        claim_cases=claim_cases,
        retrievers=retrievers,
        extract_fn=extract_fn,
        ruleset_version=RULESET_VERSION,
    )

    assert report.ruleset_version == RULESET_VERSION
    assert [result.retriever_name for result in report.track_a] == ["direct", "chroma"]
    assert [result.f1 for result in report.track_a] == [1.0, 1.0]
    assert report.track_b.n_claims == 1
    assert report.track_b.accuracy == 1.0


def test_track_b_fixture_adjudicates_all_claims_exactly():
    rules = [
        _rule("93000", "93005", ModifierIndicator.NOT_ALLOWED),
        _rule("11042", "97597", ModifierIndicator.ALLOWED),
    ]
    cases = [
        _case(
            _claim(_line("L1", "93000"), _line("L2", "93005")),
            {"L1": DispositionStatus.PAY, "L2": DispositionStatus.DENY},
        ),
        _case(
            _claim(_line("L1", "11042"), _line("L2", "97597", modifiers=["59"])),
            {"L1": DispositionStatus.PAY, "L2": DispositionStatus.FLAG},
        ),
        _case(
            _claim(
                _line("L1", "93000", dos=date(2026, 7, 15)),
                _line("L2", "93005", dos=date(2026, 7, 16)),
            ),
            {"L1": DispositionStatus.PAY, "L2": DispositionStatus.PAY},
        ),
    ]

    result = score_track_b(cases, rules, RULESET_VERSION)

    assert result.accuracy == 1.0
    assert result.n_correct == result.n_claims == 3
    assert result.confusion == {
        "expected=pay,predicted=pay": 4,
        "expected=deny,predicted=deny": 1,
        "expected=flag,predicted=flag": 1,
    }


def test_gold_rows_round_trip_to_track_b_deny_and_modifier_review_cases():
    rows = [
        _gold_row(expected_decision="DENY_COLUMN_TWO"),
        _gold_row(
            expected_decision="ALLOW_WITH_MODIFIER_REVIEW",
            column_1="11042",
            column_2="97597",
            modifier_indicator="1",
            line2_modifiers="59",
        ),
    ]
    cases, excluded = _claim_cases_from_gold_rows(rows)
    rules = [
        _rule("93000", "93005", ModifierIndicator.NOT_ALLOWED),
        _rule("11042", "97597", ModifierIndicator.ALLOWED),
    ]

    result = score_track_b(cases, rules, RULESET_VERSION)

    assert excluded == 0
    assert cases[1][0].lines[1].modifiers == ["59"]
    assert result.accuracy == 1.0
    assert result.confusion["expected=deny,predicted=deny"] == 1
    assert result.confusion["expected=flag,predicted=flag"] == 1


def test_track_b_wrong_expected_status_is_caught_by_confusion():
    cases = [
        _case(
            _claim(_line("L1", "93000"), _line("L2", "93005")),
            {"L1": DispositionStatus.PAY, "L2": DispositionStatus.PAY},
        )
    ]

    result = score_track_b(
        cases,
        [_rule("93000", "93005", ModifierIndicator.NOT_ALLOWED)],
        RULESET_VERSION,
    )

    assert result.accuracy == 0.0
    assert result.n_correct == 0
    assert result.confusion["expected=pay,predicted=deny"] == 1


def test_track_b_mismatched_line_sets_score_incorrect_without_crashing():
    cases = [
        _case(
            _claim(_line("L1", "93000"), _line("L2", "93005")),
            {"L1": DispositionStatus.PAY, "L99": DispositionStatus.PAY},
        ),
        _case(
            _claim(_line("L1", "93000"), _line("L2", "93005")),
            {"L1": DispositionStatus.PAY},
        ),
    ]

    result = score_track_b(
        cases,
        [_rule("93000", "93005", ModifierIndicator.NOT_ALLOWED)],
        RULESET_VERSION,
    )

    assert result.accuracy == 0.0
    assert result.confusion["expected=pay,predicted=missing"] == 1
    assert result.confusion["expected=missing,predicted=deny"] == 2


def test_track_b_scoring_path_does_not_import_a_model(monkeypatch):
    original_import = builtins.__import__

    def fail_on_model_import(name, *args, **kwargs):
        if name == "anthropic":
            raise AssertionError("Track B must not import Anthropic")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_on_model_import)

    result = score_track_b(
        [
            _case(
                _claim(_line("L1", "93000"), _line("L2", "93005")),
                {"L1": DispositionStatus.PAY, "L2": DispositionStatus.DENY},
            )
        ],
        [_rule("93000", "93005", ModifierIndicator.NOT_ALLOWED)],
        RULESET_VERSION,
    )

    assert result.accuracy == 1.0


def test_expected_decision_mapping_is_exact_for_gold_rows():
    assert _status_for_expected_decision("DENY_COLUMN_TWO") is DispositionStatus.DENY
    assert _status_for_expected_decision("DENY_COLUMN_TWO_MODIFIER_NOT_ALLOWED") is (
        DispositionStatus.DENY
    )
    assert _status_for_expected_decision("ALLOW_WITH_MODIFIER_REVIEW") is DispositionStatus.FLAG
    assert _status_for_expected_decision("ALLOW_DIFFERENT_DATE") is DispositionStatus.PAY
    assert _status_for_expected_decision("ALLOW_DIFFERENT_BENEFICIARY") is DispositionStatus.PAY
    assert _status_for_expected_decision("ALLOW_NO_ACTIVE_PTP_EDIT") is DispositionStatus.PAY
    assert _status_for_expected_decision("ALLOW_INACTIVE_EDIT_FOR_DOS") is DispositionStatus.PAY


def test_uncertain_gold_rows_are_excluded_from_track_b_accuracy():
    cases, excluded = _claim_cases_from_gold_rows(
        [_gold_row(expected_decision="UNCERTAIN_REVIEW_REQUIRED")]
    )

    assert cases == []
    assert excluded == 1


def test_non_ptp_source_rows_do_not_become_track_a_gold_rules():
    rows = [
        _gold_row(expected_decision="DENY_COLUMN_TWO"),
        _gold_row(
            expected_decision="ALLOW_NO_ACTIVE_PTP_EDIT",
            column_1="80053",
            column_2="36415",
            is_source_ptp_pair="0",
        ),
    ]

    rules = _rules_from_gold_rows(rows)
    result = score_track_a(
        [_candidate("93000", "93005", ModifierIndicator.NOT_ALLOWED)],
        rules,
        "direct",
    )

    assert [rule.rule_id for rule in rules] == ["PTP:93000:93005"]
    assert result.n_examples == 1
    assert result.f1 == 1.0


def test_different_beneficiary_gold_row_becomes_separate_single_line_claims():
    cases, excluded = _claim_cases_from_gold_rows(
        [
            _gold_row(
                expected_decision="ALLOW_DIFFERENT_BENEFICIARY",
                member_id_col1="M1",
                member_id_col2="M2",
            )
        ]
    )

    assert excluded == 0
    assert len(cases) == 2
    assert [len(claim.lines) for claim, _ in cases] == [1, 1]
    assert [claim.beneficiary_id for claim, _ in cases] == ["M1", "M2"]
    assert all(list(expected.values()) == [DispositionStatus.PAY] for _, expected in cases)
