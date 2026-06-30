"""PolicyForge canonical schemas — the single source of truth for every phase.

Every other module imports its data shapes from here and ONLY from here. If a
field is wrong in this file it is wrong everywhere, so this file is the one place
where correctness is non-negotiable. Do not add a field that does not exist in the
real CMS NCCI PTP data or that no phase consumes.

Grounding: the published Medicare NCCI Practitioner PTP edit file has seven
columns per edit. The models below mirror them exactly:
    1. Column 1 .................. HCPCS/CPT code eligible for payment
    2. Column 2 .................. HCPCS/CPT code denied when billed with Column 1
    3. "* in existence prior to 1996" flag
    4. Effective date ........... YYYYMMDD
    5. Deletion date ............ YYYYMMDD, or absent while the edit is active
    6. Modifier indicator ....... 0 = not allowed, 1 = allowed, 9 = not applicable
    7. PTP edit rationale ....... short free-text reason

Source: https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# A HCPCS/CPT code is five characters: CPT (5 digits, e.g. 11042), HCPCS Level II
# (letter + 4 digits, e.g. G0471), or Category III (4 digits + T, e.g. 0591T).
_CODE_PATTERN = r"^[0-9A-Z]{5}$"
# A claim-line modifier is a two-character alphanumeric token (e.g. 59, XS, 25).
_MODIFIER_PATTERN = r"^[0-9A-Z]{2}$"


# --------------------------------------------------------------------------- #
# Enums                                                                        #
# --------------------------------------------------------------------------- #
class ModifierIndicator(int, Enum):
    """CCMI — Correct Coding Modifier Indicator on a PTP edit.

    Drives whether a Column 2 denial can be bypassed:
        NOT_ALLOWED   (0): no modifier bypasses this edit. Hard deny.
        ALLOWED       (1): an NCCI-associated modifier MAY bypass the edit if
                           clinically appropriate and documented.
        NOT_APPLICABLE(9): the edit is deleted; the indicator carries no meaning.
    """

    NOT_ALLOWED = 0
    ALLOWED = 1
    NOT_APPLICABLE = 9


class DispositionStatus(str, Enum):
    """The decision the deterministic engine reaches for one claim line."""

    PAY = "pay"
    DENY = "deny"
    FLAG = "flag"  # payable but routed to a human (e.g. modifier present, verify docs)


# --------------------------------------------------------------------------- #
# Authoritative ruleset — loaded from the published CMS table (ground truth)   #
# --------------------------------------------------------------------------- #
class PTPRule(BaseModel):
    """One NCCI Procedure-to-Procedure edit, mirroring the published file 1:1.

    This is the AUTHORITATIVE rule. It is loaded from the CMS table in Phase 1 and
    compiled into the deterministic engine in Phase 4. It is also the answer key
    that Track A scores LLM extraction against.
    """

    column_1: str = Field(..., description="HCPCS/CPT code eligible for payment")
    column_2: str = Field(..., description="HCPCS/CPT code denied unless a modifier applies")
    modifier_indicator: ModifierIndicator
    effective_date: date
    deletion_date: Optional[date] = Field(
        default=None, description="None means the edit is currently active"
    )
    rationale: str = Field(..., min_length=1)
    in_existence_prior_1996: bool = False

    @field_validator("column_1", "column_2")
    @classmethod
    def _codes_well_formed(cls, v: str) -> str:
        import re

        v = v.strip().upper()
        if not re.match(_CODE_PATTERN, v):
            raise ValueError(f"not a valid 5-character HCPCS/CPT code: {v!r}")
        return v

    @model_validator(mode="after")
    def _pair_and_dates_consistent(self) -> "PTPRule":
        if self.column_1 == self.column_2:
            raise ValueError("column_1 and column_2 must differ")
        if self.deletion_date is not None and self.deletion_date < self.effective_date:
            raise ValueError("deletion_date precedes effective_date")
        return self

    @property
    def rule_id(self) -> str:
        """Stable provenance handle cited on every disposition this rule drives."""
        return f"PTP:{self.column_1}:{self.column_2}"

    def is_active_on(self, service_date: date) -> bool:
        """True if this edit governs a claim with the given date of service."""
        if service_date < self.effective_date:
            return False
        if self.deletion_date is not None and service_date >= self.deletion_date:
            return False
        return True

    def to_json_logic(self) -> dict:
        """Serialize the edit to a portable JSON Logic rule.

        This is the literal 'conversion of written policy into a rule/model' the
        project is about: a row of policy becomes a machine-evaluable expression
        that any engine — not just ours — can run. Kept intentionally simple.
        """
        pair_present = {
            "and": [
                {"in": [self.column_1, {"var": "line_codes"}]},
                {"in": [self.column_2, {"var": "line_codes"}]},
            ]
        }
        if self.modifier_indicator is ModifierIndicator.ALLOWED:
            decision = {
                "if": [
                    {"var": "column_2_has_bypass_modifier"},
                    DispositionStatus.FLAG.value,
                    DispositionStatus.DENY.value,
                ]
            }
        else:
            decision = DispositionStatus.DENY.value
        return {
            "rule_id": self.rule_id,
            "when": pair_present,
            "then_column_2": decision,
        }


# --------------------------------------------------------------------------- #
# Claims — the input to the deterministic engine                              #
# --------------------------------------------------------------------------- #
class ClaimLine(BaseModel):
    line_id: str
    code: str
    units: int = Field(default=1, ge=1)
    modifiers: list[str] = Field(default_factory=list)
    date_of_service: date

    @field_validator("code")
    @classmethod
    def _code_well_formed(cls, v: str) -> str:
        import re

        v = v.strip().upper()
        if not re.match(_CODE_PATTERN, v):
            raise ValueError(f"not a valid 5-character HCPCS/CPT code: {v!r}")
        return v

    @field_validator("modifiers")
    @classmethod
    def _modifiers_well_formed(cls, v: list[str]) -> list[str]:
        import re

        out = []
        for m in v:
            m = m.strip().upper()
            if not re.match(_MODIFIER_PATTERN, m):
                raise ValueError(f"not a valid 2-character modifier: {m!r}")
            out.append(m)
        return out


class Claim(BaseModel):
    """A professional (Part B) claim. PTP edits apply within one claim across lines
    sharing the same beneficiary, provider, and date of service."""

    claim_id: str
    beneficiary_id: str
    provider_id: str
    lines: list[ClaimLine] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _line_ids_unique(self) -> "Claim":
        ids = [ln.line_id for ln in self.lines]
        if len(ids) != len(set(ids)):
            raise ValueError("line_id values must be unique within a claim")
        return self


# --------------------------------------------------------------------------- #
# Dispositions — the output of the deterministic engine                       #
# --------------------------------------------------------------------------- #
class LineDisposition(BaseModel):
    line_id: str
    code: str
    status: DispositionStatus
    reason_code: Optional[str] = Field(
        default=None, description="Payer remit code, e.g. CO-97 (bundling)"
    )
    cited_rule_id: Optional[str] = Field(
        default=None, description="Provenance: which PTPRule drove this decision"
    )
    explanation: str


class ClaimDisposition(BaseModel):
    """Every disposition is auditable: each denied/flagged line names the rule that
    caused it and the ruleset version in force. This is the property that makes the
    engine defensible in a provider appeal — and the reason the LLM is kept out of it."""

    claim_id: str
    ruleset_version: str
    line_dispositions: list[LineDisposition]


# --------------------------------------------------------------------------- #
# LLM extraction output — candidates, gated before they become rules          #
# --------------------------------------------------------------------------- #
class RuleCandidate(BaseModel):
    """A rule the LLM proposes from policy-manual prose (Phase 3).

    A candidate is NOT a rule. It carries the provenance and self-reported
    confidence needed for (a) Track A scoring against the authoritative table and
    (b) the human gate in Phase 6. It only becomes a PTPRule after a human approves.

    Note: the manual states many rules as specialty principles, not code pairs.
    Candidates are the subset where the prose names concrete codes — those are the
    eval-able ones. Principle-level guidance is out of scope for Phase 0..7.
    """

    column_1: str
    column_2: str
    modifier_indicator: ModifierIndicator
    rationale: str
    source_chapter: str = Field(..., description="Manual chapter the rule came from")
    source_quote: str = Field(..., description="The sentence the rule was derived from")
    extraction_confidence: float = Field(..., ge=0.0, le=1.0)

    @field_validator("column_1", "column_2")
    @classmethod
    def _codes_well_formed(cls, v: str) -> str:
        import re

        v = v.strip().upper()
        if not re.match(_CODE_PATTERN, v):
            raise ValueError(f"not a valid 5-character HCPCS/CPT code: {v!r}")
        return v

    def matches(self, rule: PTPRule) -> bool:
        """A candidate is a true positive iff the code pair AND modifier indicator
        match an authoritative rule. Rationale text is not scored — wording varies."""
        return (
            self.column_1 == rule.column_1
            and self.column_2 == rule.column_2
            and self.modifier_indicator == rule.modifier_indicator
        )


# --------------------------------------------------------------------------- #
# Evaluation — the seam (Phase 5)                                              #
# --------------------------------------------------------------------------- #
class TrackAResult(BaseModel):
    """Extraction fidelity for ONE retriever arm (the Chroma ablation lives here:
    one TrackAResult for 'direct', one for 'chroma', compared by the delta)."""

    retriever_name: str
    precision: float = Field(..., ge=0.0, le=1.0)
    recall: float = Field(..., ge=0.0, le=1.0)
    f1: float = Field(..., ge=0.0, le=1.0)
    true_positives: int = Field(..., ge=0)
    false_positives: int = Field(..., ge=0)
    false_negatives: int = Field(..., ge=0)
    n_examples: int = Field(..., ge=0)


class TrackBResult(BaseModel):
    """Adjudication correctness: synthetic claims scored against the authoritative
    table + modifier logic. 100% checkable — the un-fakeable north star."""

    n_claims: int = Field(..., ge=0)
    n_correct: int = Field(..., ge=0)
    accuracy: float = Field(..., ge=0.0, le=1.0)
    confusion: dict[str, int] = Field(
        default_factory=dict, description="keys like 'expected=deny,predicted=pay'"
    )


class EvalReport(BaseModel):
    ruleset_version: str
    track_a: list[TrackAResult]
    track_b: TrackBResult
