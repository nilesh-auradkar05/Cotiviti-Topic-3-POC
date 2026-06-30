"""Human gate for promoting extracted candidates into approved PTP rules."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
import warnings

from policyforge.schemas import PTPRule, RuleCandidate


class GateDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


def review_candidate(
    candidate: RuleCandidate,
    decision: GateDecision,
    *,
    effective_date: date,
    deletion_date: date | None = None,
    in_existence_prior_1996: bool = False,
) -> PTPRule | None:
    if decision is GateDecision.REJECT:
        return None
    return PTPRule(
        column_1=candidate.column_1,
        column_2=candidate.column_2,
        modifier_indicator=candidate.modifier_indicator,
        effective_date=effective_date,
        deletion_date=deletion_date,
        rationale=candidate.rationale,
        in_existence_prior_1996=in_existence_prior_1996,
    )


def gate_node(state, config=None) -> dict:
    interrupt = _interrupt()
    candidate = state["candidate"]
    resume = interrupt(
        {
            "candidate": candidate.model_dump(mode="json"),
            "ruleset_version": state["ruleset_version"],
        }
    )
    decision = GateDecision(resume["decision"])
    if decision is GateDecision.REJECT:
        return {"approved_rule_id": None, "gate_decision": decision.value}

    rule = review_candidate(
        candidate,
        decision,
        effective_date=_date_value(resume["effective_date"]),
        deletion_date=_optional_date(resume.get("deletion_date")),
        in_existence_prior_1996=resume.get("in_existence_prior_1996", False),
    )
    store = state.get("store")
    if store is None:
        store = config["configurable"]["store"]
    # The audit timestamp must reflect the human decision (the resume), not when the
    # pipeline reached the gate. Prefer the resume payload; fall back to pipeline state.
    approved_at = _approved_at(resume, state)
    store.add_approved(
        rule,
        candidate,
        ruleset_version=state["ruleset_version"],
        approver=state["approver"],
        approved_at=approved_at,
        quote_grounded=state.get("quote_grounded", True),
    )
    return {
        "approved_rule_id": rule.rule_id,
        "gate_decision": decision.value,
        "approved_at": approved_at,
    }


def _interrupt():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from langgraph.types import interrupt

    return interrupt


def _approved_at(resume, state) -> datetime:
    value = resume.get("approved_at")
    if value is not None:
        return _datetime_value(value)
    return state["approved_at"]


def _datetime_value(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _optional_date(value) -> date | None:
    if value is None:
        return None
    return _date_value(value)


def _date_value(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)
