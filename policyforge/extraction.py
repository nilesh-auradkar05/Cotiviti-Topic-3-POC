"""Phase 3 extraction from policy prose into validated candidates."""

from __future__ import annotations

from collections.abc import Mapping
import os
import re
from typing import Any

from pydantic import BaseModel

from policyforge.schemas import ModifierIndicator, RuleCandidate


_TOOL_NAME = "record_rule_candidates"
_DEFAULT_MODEL = "claude-sonnet-4-6"


class _CandidateToolInput(BaseModel):
    candidates: list[dict[str, Any]]


def extract_rules(
    text: str,
    source_chapter: str,
    *,
    client=None,
    model: str | None = None,
) -> list[RuleCandidate]:
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    response = client.messages.create(
        model=model or os.environ.get("POLICYFORGE_EXTRACTION_MODEL", _DEFAULT_MODEL),
        max_tokens=2000,
        tools=[_rule_candidate_tool()],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[
            {
                "role": "user",
                "content": (
                    "Extract only explicit NCCI PTP code-pair candidates from this policy text. "
                    "Return a candidate only when the prose names the Column 1 code, Column 2 "
                    "code, and modifier indicator. Use a verbatim source_quote from the text. "
                    "Do not complete pairs from prior knowledge. If the text names no complete "
                    "candidate, return an empty candidates list.\n\n"
                    f"Policy text:\n{text}"
                ),
            }
        ],
    )

    candidates = []
    for payload in _candidate_payloads(response):
        fields = dict(payload)
        fields["source_chapter"] = source_chapter
        candidates.append(RuleCandidate(**fields))
    return candidates


def is_quote_grounded(candidate: RuleCandidate, text: str) -> bool:
    """True if the candidate's source_quote appears in the policy text.

    PDF-extracted prose carries hard line breaks and irregular spacing, so a quote
    the model copied verbatim ("Column One") will not substring-match the raw text
    ("Column\\nOne"). We compare on whitespace-normalized forms so a genuinely
    grounded quote reads as grounded instead of failing on layout artifacts.
    """
    return _normalize_whitespace(candidate.source_quote) in _normalize_whitespace(text)


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _candidate_payloads(response: Any) -> list[Mapping[str, Any]]:
    candidates = []
    for block in _get(response, "content", []):
        if _get(block, "type") != "tool_use" or _get(block, "name") != _TOOL_NAME:
            continue
        raw_input = _get(block, "input", {})
        if raw_input == {}:
            continue
        tool_input = _CandidateToolInput(**raw_input)
        candidates.extend(tool_input.candidates)
    return candidates


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _rule_candidate_tool() -> dict:
    candidate_schema = RuleCandidate.model_json_schema()
    candidate_schema.pop("$defs", None)
    candidate_schema["properties"].pop("source_chapter")
    candidate_schema["properties"]["modifier_indicator"] = {
        "type": "integer",
        "enum": [indicator.value for indicator in ModifierIndicator],
    }
    candidate_schema["required"].remove("source_chapter")
    candidate_schema["additionalProperties"] = False
    return {
        "name": _TOOL_NAME,
        "description": "Record explicit NCCI PTP rule candidates from the supplied policy text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": candidate_schema,
                }
            },
            "required": ["candidates"],
            "additionalProperties": False,
        },
    }
