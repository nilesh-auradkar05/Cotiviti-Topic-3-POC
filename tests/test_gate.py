"""Phase 6 human gate and rule-store tests."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, TypedDict
import warnings

import pytest
from pydantic import ValidationError

from policyforge.engine import adjudicate
from policyforge.gate import GateDecision, gate_node, review_candidate
from policyforge.schemas import (
    Claim,
    ClaimLine,
    DispositionStatus,
    ModifierIndicator,
    PTPRule,
    RuleCandidate,
)
from policyforge.store import RuleStore


RULESET_VERSION = "candidate-rules-v1"
APPROVED_AT = datetime(2026, 7, 1, 12, 30, tzinfo=timezone.utc)
RESUMED_AT = datetime(2026, 7, 2, 9, 15, tzinfo=timezone.utc)
warnings.filterwarnings(
    "ignore",
    category=Warning,
    module=r"langgraph\.checkpoint\.base",
)
pytestmark = pytest.mark.filterwarnings(
    "ignore::langchain_core._api.deprecation.LangChainPendingDeprecationWarning"
)


class GateState(TypedDict, total=False):
    candidate: RuleCandidate
    store: RuleStore
    ruleset_version: str
    approver: str
    approved_at: datetime
    quote_grounded: bool
    approved_rule_id: str | None
    gate_decision: str


def _candidate(**overrides) -> RuleCandidate:
    fields = {
        "column_1": "93000",
        "column_2": "93005",
        "modifier_indicator": ModifierIndicator.NOT_ALLOWED,
        "rationale": "Misuse of column two code with column one code",
        "source_chapter": "Chapter 11",
        "source_quote": "CPT code 93000 is the Column 1 code and 93005 is Column 2.",
        "extraction_confidence": 0.87,
    }
    fields.update(overrides)
    return RuleCandidate(**fields)


def _authoritative_rule(**overrides) -> PTPRule:
    fields = {
        "column_1": "11042",
        "column_2": "97597",
        "modifier_indicator": ModifierIndicator.ALLOWED,
        "effective_date": date(2020, 1, 1),
        "rationale": "Standards of medical and surgical practice",
    }
    fields.update(overrides)
    return PTPRule(**fields)


def _claim(column_1: str, column_2: str, *, modifiers: list[str] | None = None) -> Claim:
    return Claim(
        claim_id="C1",
        beneficiary_id="B1",
        provider_id="P1",
        lines=[
            ClaimLine(line_id="L1", code=column_1, date_of_service=date(2026, 7, 1)),
            ClaimLine(
                line_id="L2",
                code=column_2,
                modifiers=[] if modifiers is None else modifiers,
                date_of_service=date(2026, 7, 1),
            ),
        ],
    )


def test_review_candidate_rejects_to_none_and_builds_no_rule():
    # Unit level: reject yields no rule and constructs nothing. The store-level guarantee
    # ("an unapproved candidate never reaches the store") is exercised behaviorally through
    # gate_node in test_langgraph_gate_..._reject_resume_keeps_store_empty below.
    candidate = _candidate()

    rule = review_candidate(
        candidate,
        GateDecision.REJECT,
        effective_date=date(2020, 1, 1),
    )

    assert rule is None


def test_an_approved_candidate_becomes_a_loadable_adjudicable_rule():
    store = RuleStore(":memory:")
    candidate = _candidate()
    rule = review_candidate(
        candidate,
        GateDecision.APPROVE,
        effective_date=date(2020, 1, 1),
    )

    store.add_approved(
        rule,
        candidate,
        ruleset_version=RULESET_VERSION,
        approver="ncci-reviewer",
        approved_at=APPROVED_AT,
        quote_grounded=True,
    )

    rules = store.load_ruleset(RULESET_VERSION)
    assert rules == [
        PTPRule(
            column_1=candidate.column_1,
            column_2=candidate.column_2,
            modifier_indicator=candidate.modifier_indicator,
            effective_date=date(2020, 1, 1),
            rationale=candidate.rationale,
        )
    ]
    disposition = adjudicate(
        _claim("93000", "93005"),
        rules,
        RULESET_VERSION,
    )
    line_two = {line.line_id: line for line in disposition.line_dispositions}["L2"]
    assert line_two.status is DispositionStatus.DENY
    assert line_two.cited_rule_id == "PTP:93000:93005"
    assert disposition.ruleset_version == RULESET_VERSION


def test_an_inconsistent_approval_is_rejected_by_ptp_rule_validation():
    # Construction-is-the-gate at the unit level: a contradictory approval can never
    # produce a PTPRule. The "nothing is written" half is proven through gate_node in
    # test_gate_node_writes_nothing_when_an_approved_rule_fails_validation below.
    with pytest.raises(ValidationError):
        review_candidate(
            _candidate(),
            GateDecision.APPROVE,
            effective_date=date(2026, 7, 1),
            deletion_date=date(2025, 12, 31),
        )


def test_store_roundtrips_authoritative_rules_through_ptp_rule_validation():
    store = RuleStore(":memory:")
    rule = _authoritative_rule()

    store.seed_authoritative([rule], ruleset_version=RULESET_VERSION)

    loaded = store.load_ruleset(RULESET_VERSION)
    assert loaded == [rule]
    assert loaded[0].is_active_on(date(2026, 7, 1)) is True
    assert loaded[0].to_json_logic() == rule.to_json_logic()
    provenance = store.provenance_for(rule.rule_id, RULESET_VERSION)
    assert provenance["origin"] == "authoritative"
    assert provenance["json_logic"] == rule.to_json_logic()


def test_store_versions_keep_approved_rules_in_their_ruleset_label():
    store = RuleStore(":memory:")
    candidate = _candidate()
    rule = review_candidate(candidate, GateDecision.APPROVE, effective_date=date(2020, 1, 1))

    store.seed_authoritative([_authoritative_rule()], ruleset_version="v1")
    store.add_approved(
        rule,
        candidate,
        ruleset_version="v2",
        approver="ncci-reviewer",
        approved_at=APPROVED_AT,
        quote_grounded=True,
    )

    assert [loaded.rule_id for loaded in store.load_ruleset("v1")] == ["PTP:11042:97597"]
    assert [loaded.rule_id for loaded in store.load_ruleset("v2")] == ["PTP:93000:93005"]
    assert store.versions() == ["v1", "v2"]


def test_authoritative_and_human_gated_rules_coexist_in_one_ruleset():
    store = RuleStore(":memory:")
    authoritative = _authoritative_rule()
    candidate = _candidate()
    approved = review_candidate(candidate, GateDecision.APPROVE, effective_date=date(2020, 1, 1))

    store.seed_authoritative([authoritative], ruleset_version=RULESET_VERSION)
    store.add_approved(
        approved,
        candidate,
        ruleset_version=RULESET_VERSION,
        approver="ncci-reviewer",
        approved_at=APPROVED_AT,
        quote_grounded=True,
    )

    assert [rule.rule_id for rule in store.load_ruleset(RULESET_VERSION)] == [
        "PTP:11042:97597",
        "PTP:93000:93005",
    ]
    assert store.provenance_for(authoritative.rule_id, RULESET_VERSION)["origin"] == "authoritative"
    assert store.provenance_for(approved.rule_id, RULESET_VERSION)["origin"] == "human_gated"


def test_human_gated_provenance_records_low_trust_ungrounded_candidates():
    store = RuleStore(":memory:")
    candidate = _candidate(source_quote="This quote was not found in the source chapter.")
    rule = review_candidate(candidate, GateDecision.APPROVE, effective_date=date(2020, 1, 1))

    store.add_approved(
        rule,
        candidate,
        ruleset_version=RULESET_VERSION,
        approver="ncci-reviewer",
        approved_at=APPROVED_AT,
        quote_grounded=False,
    )

    provenance = store.provenance_for(rule.rule_id, RULESET_VERSION)
    assert provenance["origin"] == "human_gated"
    assert provenance["approver"] == "ncci-reviewer"
    assert provenance["approved_at"] == APPROVED_AT.isoformat()
    assert provenance["source_chapter"] == candidate.source_chapter
    assert provenance["source_quote"] == candidate.source_quote
    assert provenance["extraction_confidence"] == candidate.extraction_confidence
    assert provenance["quote_grounded"] is False


def test_langgraph_gate_interrupts_before_writing_and_approve_resume_writes_one_rule():
    graph = _gate_graph()
    store = RuleStore(":memory:")
    config = {"configurable": {"thread_id": "phase6-approve", "store": store}}
    state: GateState = {
        "candidate": _candidate(),
        "ruleset_version": RULESET_VERSION,
        "approver": "ncci-reviewer",
        "approved_at": APPROVED_AT,
        "quote_grounded": True,
    }

    chunks = list(graph.stream(state, config))

    assert "__interrupt__" in chunks[0]
    assert chunks[0]["__interrupt__"][0].value["candidate"]["column_1"] == "93000"
    assert store.load_ruleset(RULESET_VERSION) == []

    resumed = list(
        graph.stream(
            _command(
                {
                    "decision": "approve",
                    "effective_date": "2020-01-01",
                    "deletion_date": None,
                    "in_existence_prior_1996": False,
                }
            ),
            config,
        )
    )

    assert resumed[-1]["gate"]["approved_rule_id"] == "PTP:93000:93005"
    assert [rule.rule_id for rule in store.load_ruleset(RULESET_VERSION)] == ["PTP:93000:93005"]


def test_langgraph_gate_interrupts_before_writing_and_reject_resume_keeps_store_empty():
    graph = _gate_graph()
    store = RuleStore(":memory:")
    config = {"configurable": {"thread_id": "phase6-reject", "store": store}}
    state: GateState = {
        "candidate": _candidate(),
        "ruleset_version": RULESET_VERSION,
        "approver": "ncci-reviewer",
        "approved_at": APPROVED_AT,
        "quote_grounded": True,
    }

    chunks = list(graph.stream(state, config))

    assert "__interrupt__" in chunks[0]
    assert store.load_ruleset(RULESET_VERSION) == []

    resumed = list(graph.stream(_command({"decision": "reject"}), config))

    assert resumed[-1]["gate"]["approved_rule_id"] is None
    assert store.load_ruleset(RULESET_VERSION) == []


def test_gate_node_writes_nothing_when_an_approved_rule_fails_validation():
    graph = _gate_graph()
    store = RuleStore(":memory:")
    config = {"configurable": {"thread_id": "phase6-invalid", "store": store}}
    state: GateState = {
        "candidate": _candidate(),
        "ruleset_version": RULESET_VERSION,
        "approver": "ncci-reviewer",
        "approved_at": APPROVED_AT,
        "quote_grounded": True,
    }

    list(graph.stream(state, config))

    with pytest.raises(ValidationError):
        list(
            graph.stream(
                _command(
                    {
                        "decision": "approve",
                        "effective_date": "2026-07-01",
                        "deletion_date": "2025-12-31",
                        "in_existence_prior_1996": False,
                    }
                ),
                config,
            )
        )

    assert store.load_ruleset(RULESET_VERSION) == []


def test_gate_records_the_resume_timestamp_not_the_pipeline_time():
    graph = _gate_graph()
    store = RuleStore(":memory:")
    config = {"configurable": {"thread_id": "phase6-approved-at", "store": store}}
    state: GateState = {
        "candidate": _candidate(),
        "ruleset_version": RULESET_VERSION,
        "approver": "ncci-reviewer",
        "approved_at": APPROVED_AT,  # stamped earlier, as the pipeline reached the gate
        "quote_grounded": True,
    }

    list(graph.stream(state, config))
    list(
        graph.stream(
            _command(
                {
                    "decision": "approve",
                    "effective_date": "2020-01-01",
                    "deletion_date": None,
                    "in_existence_prior_1996": False,
                    "approved_at": RESUMED_AT.isoformat(),
                }
            ),
            config,
        )
    )

    provenance = store.provenance_for("PTP:93000:93005", RULESET_VERSION)
    assert provenance["approved_at"] == RESUMED_AT.isoformat()
    assert provenance["approved_at"] != APPROVED_AT.isoformat()


def _gate_graph():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph

    builder = StateGraph(GateState)
    builder.add_node("gate", gate_node)
    builder.add_edge(START, "gate")
    builder.add_edge("gate", END)
    return builder.compile(checkpointer=InMemorySaver())


def _command(resume: dict[str, Any]):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from langgraph.types import Command

    return Command(resume=resume)
