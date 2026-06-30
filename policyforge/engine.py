"""Deterministic NCCI PTP adjudication engine."""

from __future__ import annotations

from collections import defaultdict

from policyforge.schemas import (
    Claim,
    ClaimDisposition,
    DispositionStatus,
    LineDisposition,
    ModifierIndicator,
    PTPRule,
)


NCCI_PTP_ASSOCIATED_MODIFIERS = {
    "E1", "E2", "E3", "E4",
    "FA", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9",
    "TA", "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9",
    "LT", "RT", "LC", "LD", "RC", "LM", "RI",
    "24", "25", "57", "58", "78", "79",
    "27", "59", "91",
    "XE", "XP", "XS", "XU",
}
_SEVERITY = {
    DispositionStatus.PAY: 0,
    DispositionStatus.FLAG: 1,
    DispositionStatus.DENY: 2,
}


def adjudicate(
    claim: Claim,
    rules: list[PTPRule],
    ruleset_version: str,
) -> ClaimDisposition:
    rules_by_pair = defaultdict(list)
    for rule in rules:
        rules_by_pair[(rule.column_1, rule.column_2)].append(rule)

    dispositions = {
        line.line_id: LineDisposition(
            line_id=line.line_id,
            code=line.code,
            status=DispositionStatus.PAY,
            explanation="No active NCCI PTP edit applies.",
        )
        for line in claim.lines
    }

    for column_1_line in claim.lines:
        for column_2_line in claim.lines:
            if column_1_line.line_id == column_2_line.line_id:
                continue
            if column_1_line.date_of_service != column_2_line.date_of_service:
                continue
            active_rules = [
                rule
                for rule in rules_by_pair.get((column_1_line.code, column_2_line.code), [])
                if rule.is_active_on(column_1_line.date_of_service)
            ]
            if not active_rules:
                continue
            rule = min(active_rules, key=lambda active_rule: active_rule.rule_id)
            if rule.modifier_indicator is ModifierIndicator.NOT_APPLICABLE:
                continue

            candidate = _candidate_disposition(column_2_line, rule)
            current = dispositions[column_2_line.line_id]
            if _beats(candidate, current):
                dispositions[column_2_line.line_id] = candidate

    return ClaimDisposition(
        claim_id=claim.claim_id,
        ruleset_version=ruleset_version,
        line_dispositions=[dispositions[line.line_id] for line in claim.lines],
    )


def _candidate_disposition(line, rule: PTPRule) -> LineDisposition:
    bypass_modifiers = sorted(set(line.modifiers) & NCCI_PTP_ASSOCIATED_MODIFIERS)
    if rule.modifier_indicator is ModifierIndicator.ALLOWED and bypass_modifiers:
        modifier_list = ", ".join(bypass_modifiers)
        return LineDisposition(
            line_id=line.line_id,
            code=line.code,
            status=DispositionStatus.FLAG,
            cited_rule_id=rule.rule_id,
            explanation=(
                f"NCCI PTP edit {rule.rule_id} allows bypass review because column-2 "
                f"line has modifier {modifier_list}."
            ),
        )

    return LineDisposition(
        line_id=line.line_id,
        code=line.code,
        status=DispositionStatus.DENY,
        reason_code="CO-97",
        cited_rule_id=rule.rule_id,
        explanation=(
            f"NCCI PTP edit {rule.rule_id} bundles column-2 code {rule.column_2} "
            f"into column-1 code {rule.column_1}."
        ),
    )


def _beats(candidate: LineDisposition, current: LineDisposition) -> bool:
    candidate_severity = _SEVERITY[candidate.status]
    current_severity = _SEVERITY[current.status]
    if candidate_severity != current_severity:
        return candidate_severity > current_severity
    if candidate.cited_rule_id is None:
        return False
    if current.cited_rule_id is None:
        return True
    return candidate.cited_rule_id < current.cited_rule_id
