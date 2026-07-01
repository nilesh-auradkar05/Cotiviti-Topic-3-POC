"""Phase 3 extraction tests."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from policyforge.extraction import _rule_candidate_tool, extract_rules, is_quote_grounded
from policyforge.schemas import ModifierIndicator, PTPRule


QUOTE = (
    "CPT code 11042 is the Column 1 code and CPT code 97597 is the Column 2 code; "
    "the modifier indicator is 1."
)
TEXT = f"{QUOTE} The edit is based on standards of medical and surgical practice."


class FakeClient:
    def __init__(self, candidates: list[dict] | None = None, *, blocks: list[dict] | None = None) -> None:
        self.candidates = [] if candidates is None else candidates
        self.blocks = blocks
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.blocks is not None:
            return {"content": self.blocks}
        return {
            "content": [
                {
                    "type": "tool_use",
                    "name": "record_rule_candidates",
                    "input": {"candidates": self.candidates},
                }
            ]
        }


def _candidate(**overrides):
    candidate = {
        "column_1": "11042",
        "column_2": "97597",
        "modifier_indicator": 1,
        "rationale": "Standards of medical and surgical practice",
        "source_quote": QUOTE,
        "extraction_confidence": 0.82,
    }
    candidate.update(overrides)
    return candidate


def test_a_pair_named_in_prose_is_extracted_with_its_ccmi_and_provenance():
    client = FakeClient([_candidate()])

    candidates = extract_rules(TEXT, "Chapter 1", client=client, model="test-model")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.column_1 == "11042"
    assert candidate.column_2 == "97597"
    assert candidate.modifier_indicator is ModifierIndicator.ALLOWED
    assert candidate.source_chapter == "Chapter 1"
    assert candidate.source_quote == QUOTE
    assert candidate.extraction_confidence == 0.82
    assert is_quote_grounded(candidate, TEXT) is True
    assert client.calls[0]["model"] == "test-model"
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Extract only explicit NCCI PTP code-pair candidates" in prompt
    assert "Do not complete pairs from prior knowledge" in prompt
    assert "If the text names no complete candidate, return an empty candidates list" in prompt
    tool_schema = client.calls[0]["tools"][0]["input_schema"]["properties"]["candidates"]["items"]
    assert "source_chapter" not in tool_schema["properties"]


def test_malformed_model_output_is_rejected_by_pydantic():
    client = FakeClient([_candidate(column_1="1104")])

    with pytest.raises(ValidationError):
        extract_rules(TEXT, "Chapter 1", client=client, model="test-model")


@pytest.mark.parametrize(
    "bad_candidate",
    [
        _candidate(modifier_indicator=7),
        _candidate(extraction_confidence=1.5),
        {key: value for key, value in _candidate().items() if key != "source_quote"},
    ],
)
def test_bad_candidate_fields_are_rejected_by_pydantic(bad_candidate):
    client = FakeClient([bad_candidate])

    with pytest.raises(ValidationError):
        extract_rules(TEXT, "Chapter 1", client=client, model="test-model")


def test_a_malformed_candidate_container_is_rejected():
    client = FakeClient(
        blocks=[
            {
                "type": "tool_use",
                "name": "record_rule_candidates",
                "input": {"candidates": "not-a-list"},
            }
        ]
    )

    with pytest.raises(ValidationError):
        extract_rules(TEXT, "Chapter 1", client=client, model="test-model")


def test_candidates_from_multiple_tool_use_blocks_are_retained():
    client = FakeClient(
        blocks=[
            {
                "type": "tool_use",
                "name": "record_rule_candidates",
                "input": {"candidates": [_candidate()]},
            },
            {
                "type": "tool_use",
                "name": "record_rule_candidates",
                "input": {
                    "candidates": [
                        _candidate(
                            column_1="93000",
                            column_2="93005",
                            modifier_indicator=0,
                            source_quote="CPT code 93000 is paired with CPT code 93005.",
                        )
                    ]
                },
            },
        ]
    )

    candidates = extract_rules(TEXT, "Chapter 1", client=client, model="test-model")

    assert [candidate.column_1 for candidate in candidates] == ["11042", "93000"]


def test_an_ungrounded_quote_is_retained_and_measurable():
    client = FakeClient([_candidate(source_quote="This sentence is not in the source text.")])

    candidates = extract_rules(TEXT, "Chapter 1", client=client, model="test-model")

    assert len(candidates) == 1
    assert candidates[0].extraction_confidence == 0.82
    assert is_quote_grounded(candidates[0], TEXT) is False


def test_grounding_tolerates_pdf_whitespace_and_line_breaks():
    # A quote the model copied verbatim, but the source PDF text carries hard line
    # breaks and doubled spaces from layout extraction. Grounding must still hold.
    client = FakeClient([_candidate(source_quote="CPT code 11042 is the Column 1 code")])
    pdf_like_text = "CPT code 11042 is the\nColumn 1  code and CPT code 97597 is the Column 2 code."

    candidates = extract_rules(pdf_like_text, "Chapter 1", client=client, model="test-model")

    assert is_quote_grounded(candidates[0], pdf_like_text) is True


def test_empty_tool_output_yields_no_candidates():
    client = FakeClient([])

    candidates = extract_rules(
        "Providers must code correctly even when no edit exists.",
        "Chapter 1",
        client=client,
        model="test-model",
    )

    assert candidates == []


def test_empty_forced_tool_input_yields_no_candidates():
    client = FakeClient(
        blocks=[
            {
                "type": "tool_use",
                "name": "record_rule_candidates",
                "input": {},
            }
        ]
    )

    candidates = extract_rules(
        "Providers must code correctly even when no edit exists.",
        "Chapter 1",
        client=client,
        model="test-model",
    )

    assert candidates == []


def test_every_ref_in_the_rule_candidate_tool_schema_resolves():
    schema = _rule_candidate_tool()["input_schema"]
    candidate_schema = schema["properties"]["candidates"]["items"]

    refs = []

    def collect_refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                refs.append(value["$ref"])
            for child in value.values():
                collect_refs(child)
        elif isinstance(value, list):
            for child in value:
                collect_refs(child)

    collect_refs(schema)

    for ref in refs:
        pointer = schema
        for part in ref.removeprefix("#/").split("/"):
            pointer = pointer[part]
        assert pointer
    assert candidate_schema["properties"]["modifier_indicator"] == {
        "type": "integer",
        "enum": [0, 1, 9],
    }


def test_an_extracted_candidate_is_scorable_against_the_authoritative_table():
    candidate = extract_rules(TEXT, "Chapter 1", client=FakeClient([_candidate()]))[0]
    authoritative = PTPRule(
        column_1="11042",
        column_2="97597",
        modifier_indicator=ModifierIndicator.ALLOWED,
        effective_date=date(1996, 1, 1),
        rationale="authoritative wording",
    )
    wrong_indicator = PTPRule(
        column_1="11042",
        column_2="97597",
        modifier_indicator=ModifierIndicator.NOT_ALLOWED,
        effective_date=date(1996, 1, 1),
        rationale="authoritative wording",
    )

    assert candidate.matches(authoritative) is True
    assert candidate.matches(wrong_indicator) is False
