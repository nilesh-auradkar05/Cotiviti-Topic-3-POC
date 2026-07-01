"""Streamlit shell for the Phase 7 PolicyForge demo."""

from __future__ import annotations

from datetime import date, datetime, timezone
import os
from pathlib import Path

import streamlit as st

from policyforge.engine import adjudicate
from policyforge.extraction import extract_rules, is_quote_grounded
from policyforge.gate import GateDecision, review_candidate
from policyforge.ingestion import load_policy_sections, load_ptp_table
from policyforge.retriever import DirectInjectionRetriever, build_chroma_index
from policyforge.schemas import Claim, ClaimLine, ModifierIndicator, PTPRule, RuleCandidate
from policyforge.store import RuleStore


ROOT = Path(__file__).resolve().parents[1]
POLICY_MANUAL_PATH = ROOT / "data" / "2026_ncci_medicare_policy_manual_all-chapters.pdf"
PTP_TABLE_DIR = ROOT / "data" / "ccipra-v322r0-f1"
FIXTURE_PTP_TABLE = ROOT / "fixtures" / "sample_ptp.csv"
DEFAULT_STORE_PATH = ROOT / "data" / "policyforge_store.db"
DEFAULT_RULESET_VERSION = "demo"
DEFAULT_LINE_1_CODE = "11042"
DEFAULT_LINE_2_CODE = "97597"
DEFAULT_QUERY = f"{DEFAULT_LINE_1_CODE} {DEFAULT_LINE_2_CODE}"
DEFAULT_RULE_ID = f"PTP:{DEFAULT_LINE_1_CODE}:{DEFAULT_LINE_2_CODE}"
DEFAULT_SERVICE_DATE = date(2026, 1, 1)
DOTENV_PATH = ROOT / ".env"


def main() -> None:
    _load_dotenv()
    st.set_page_config(page_title="PolicyForge", layout="wide")
    st.title("PolicyForge")

    store = _store()
    ruleset_version = st.sidebar.text_input("Ruleset version", DEFAULT_RULESET_VERSION)
    approver = st.sidebar.text_input("Reviewer", "demo-reviewer")

    pipeline_tab, gate_tab, adjudicate_tab = st.tabs(["Pipeline", "Gate", "Adjudicate"])
    with pipeline_tab:
        _pipeline_view(ruleset_version)
    with gate_tab:
        _gate_view(store, ruleset_version, approver)
    with adjudicate_tab:
        _adjudicate_view(store, ruleset_version)


def _pipeline_view(ruleset_version: str) -> None:
    st.caption(
        "Pipeline stages: retrieve policy manual chunks, extract draft RuleCandidate objects, "
        "then send candidates to the Gate tab."
    )
    query = st.text_input("Policy query", DEFAULT_QUERY)
    embedding_model = os.environ.get("POLICYFORGE_EMBEDDING_MODEL")
    use_chroma = st.checkbox(
        "Treatment retrieval",
        value=False,
        disabled=embedding_model is None,
    )

    if st.button("Run pipeline", type="primary"):
        retrievers = [DirectInjectionRetriever(_policy_sections())]
        if use_chroma:
            retrievers.append(
                build_chroma_index(
                    _policy_sections(),
                    collection_name=f"policyforge-{ruleset_version}",
                )
            )

        trace = []
        all_chunks = []
        for retriever in retrievers:
            chunks = retriever.retrieve(query, k=5)
            trace.append({"retriever": retriever.name, "chunks": chunks})
            all_chunks.extend(chunks)
        candidates, grounded_by_candidate, extraction_status = _extract_candidates(
            all_chunks,
            extract_rules,
            extraction_enabled=bool(os.environ.get("ANTHROPIC_API_KEY")),
        )

        st.session_state["trace"] = trace
        st.session_state["candidates"] = candidates
        st.session_state["grounded_by_candidate"] = grounded_by_candidate
        st.session_state["extraction_status"] = extraction_status
        st.session_state["pipeline_ran"] = True

    for arm in st.session_state.get("trace", []):
        summary = _retriever_summary(arm)
        st.subheader(summary["label"])
        st.caption(summary["description"])
        st.metric("Retrieved", summary["count"])
        if not arm["chunks"]:
            st.warning(summary["empty_message"])
        for chunk in arm["chunks"]:
            with st.expander(_chunk_label(chunk)):
                st.write(chunk.text)

    if st.session_state.get("pipeline_ran"):
        candidates = st.session_state.get("candidates", [])
        chunk_count = sum(len(arm["chunks"]) for arm in st.session_state.get("trace", []))
        extraction_status = st.session_state.get("extraction_status", {})
        st.subheader("Extraction")
        if candidates:
            st.caption(extraction_status.get("message", "Extraction produced candidates."))
            _candidate_table(candidates, st.session_state.get("grounded_by_candidate", {}))
        elif extraction_status.get("error"):
            st.error(extraction_status["message"])
        elif extraction_status:
            st.info(extraction_status["message"])
        elif chunk_count:
            st.info("Extraction produced 0 draft candidates.")
        else:
            st.info("No extraction ran because retrieval returned 0 policy chunks.")


def _gate_view(store: RuleStore, ruleset_version: str, approver: str) -> None:
    candidates = st.session_state.get("candidates", [])
    grounded_by_candidate = st.session_state.get("grounded_by_candidate", {})
    if not candidates:
        if st.session_state.get("pipeline_ran"):
            st.info(
                "Pipeline ran, but no draft RuleCandidate objects were extracted for review. "
                "Check the Pipeline tab retrieval and extraction summaries."
            )
        else:
            st.info("Run the pipeline to collect candidates.")
        return

    selected = st.selectbox(
        "Candidate",
        range(len(candidates)),
        format_func=lambda index: _candidate_label(candidates[index]),
    )
    candidate = candidates[selected]
    st.json(candidate.model_dump(mode="json"))

    extracted_ccmi = candidate.modifier_indicator
    if extracted_ccmi is ModifierIndicator.NOT_APPLICABLE:
        st.warning(
            "Extracted CCMI is 9 (not applicable). A rule stored with CCMI 9 is inert — "
            "the engine skips it and the pair will always pay. If the rationale forbids "
            "modifier bypass, correct this to 0 before approving."
        )
    ccmi_choice = st.selectbox(
        "Modifier indicator (CCMI) to store",
        options=[0, 1, 9],
        index=[0, 1, 9].index(extracted_ccmi.value),
        format_func=lambda value: {
            0: "0 - not allowed (hard deny)",
            1: "1 - allowed (deny; flag with NCCI modifier)",
            9: "9 - not applicable (inert; engine skips)",
        }[value],
    )
    reviewed_indicator = ModifierIndicator(ccmi_choice)
    if reviewed_indicator is not extracted_ccmi:
        st.info(f"Reviewer override: storing CCMI {ccmi_choice} instead of {extracted_ccmi.value}.")

    effective_date = st.date_input("Effective date", date(2026, 1, 1))
    deletion_date_enabled = st.checkbox("Deletion date")
    deletion_date = (
        st.date_input("Deletion date value", date(2026, 12, 31))
        if deletion_date_enabled
        else None
    )
    in_existence_prior_1996 = st.checkbox("In existence prior to 1996")

    approve, reject = st.columns(2)
    with approve:
        if st.button("Approve", type="primary"):
            rule = review_candidate(
                candidate,
                GateDecision.APPROVE,
                effective_date=effective_date,
                deletion_date=deletion_date,
                in_existence_prior_1996=in_existence_prior_1996,
                modifier_indicator=reviewed_indicator,
            )
            store.add_approved(
                rule,
                candidate,
                ruleset_version=ruleset_version,
                approver=approver,
                approved_at=datetime.now(timezone.utc),
                quote_grounded=grounded_by_candidate.get(_candidate_key(candidate), True),
            )
            st.success(f"Approved {rule.rule_id}")
    with reject:
        if st.button("Reject"):
            st.session_state.setdefault("rejected_candidates", []).append(
                candidate.model_dump(mode="json")
            )
            st.warning("Candidate rejected.")


def _adjudicate_view(store: RuleStore, ruleset_version: str) -> None:
    seeded = _ensure_authoritative_seeded(store, ruleset_version)
    rules = store.load_ruleset(ruleset_version)
    if seeded:
        st.success(f"Seeded {DEFAULT_RULE_ID} from the authoritative CMS table.")
    st.caption(
        f"Ruleset {ruleset_version!r} contains {len(rules)} rule(s). "
        f"The default claim uses the canonical demo pair {DEFAULT_QUERY}."
    )

    column_1, column_2, modifier = st.columns(3)
    with column_1:
        code_1 = st.text_input("Line 1 code", DEFAULT_LINE_1_CODE)
    with column_2:
        code_2 = st.text_input("Line 2 code", DEFAULT_LINE_2_CODE)
    with modifier:
        line_2_modifier = st.text_input("Line 2 modifier", "")
    service_date = st.date_input("Date of service", DEFAULT_SERVICE_DATE)

    if st.button("Adjudicate", type="primary"):
        claim = Claim(
            claim_id="demo-claim",
            beneficiary_id="demo-beneficiary",
            provider_id="demo-provider",
            lines=[
                ClaimLine(line_id="1", code=code_1, date_of_service=service_date),
                ClaimLine(
                    line_id="2",
                    code=code_2,
                    modifiers=[line_2_modifier] if line_2_modifier else [],
                    date_of_service=service_date,
                ),
            ],
        )
        disposition = adjudicate(claim, rules, ruleset_version)
        st.json(disposition.model_dump(mode="json"))
        for line in disposition.line_dispositions:
            if line.cited_rule_id is None:
                continue
            with st.expander(f"Provenance for {line.cited_rule_id}"):
                st.json(store.provenance_for(line.cited_rule_id, ruleset_version))


def _store() -> RuleStore:
    DEFAULT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return RuleStore(os.environ.get("POLICYFORGE_STORE_PATH", DEFAULT_STORE_PATH))


def _load_dotenv(path: Path = DOTENV_PATH) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _dotenv_value(value)


def _dotenv_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


@st.cache_data(show_spinner=False)
def _policy_sections() -> dict[str, str]:
    if not POLICY_MANUAL_PATH.exists():
        return {}
    return load_policy_sections(POLICY_MANUAL_PATH)


@st.cache_data(show_spinner=False)
def _ptp_rules() -> list[PTPRule]:
    table_source = PTP_TABLE_DIR if PTP_TABLE_DIR.exists() else FIXTURE_PTP_TABLE
    return [
        rule
        for rule in load_ptp_table(table_source)
        if rule.column_1 == DEFAULT_LINE_1_CODE and rule.column_2 == DEFAULT_LINE_2_CODE
    ]


def _ensure_authoritative_seeded(store: RuleStore, ruleset_version: str) -> int:
    if any(rule.rule_id == DEFAULT_RULE_ID for rule in store.load_ruleset(ruleset_version)):
        return 0
    rules = _ptp_rules()
    store.seed_authoritative(rules, ruleset_version=ruleset_version)
    return len(rules)


def _candidate_table(
    candidates: list[RuleCandidate],
    grounded_by_candidate: dict[tuple[str, str, ModifierIndicator], bool] | None = None,
) -> None:
    if not candidates:
        return
    grounded_by_candidate = grounded_by_candidate or {}
    st.dataframe(
        [
            {
                "column_1": candidate.column_1,
                "column_2": candidate.column_2,
                "ccmi": _ccmi_value(candidate.modifier_indicator),
                "chapter": candidate.source_chapter,
                "confidence": candidate.extraction_confidence,
                "grounded": grounded_by_candidate.get(_candidate_key(candidate), None),
                "source_quote": candidate.source_quote,
            }
            for candidate in candidates
        ],
        hide_index=True,
    )


def _extract_candidates(chunks, extract_fn, *, extraction_enabled: bool):
    if not chunks:
        return (
            [],
            {},
            {
                "attempted": False,
                "message": "No policy chunks reached extraction.",
            },
        )
    if not extraction_enabled:
        return (
            [],
            {},
            {
                "attempted": False,
                "message": (
                    "Extraction skipped because ANTHROPIC_API_KEY is not configured. "
                    "Retrieved policy text is still shown above; configure Anthropic and "
                    "rerun Pipeline to produce Gate candidates."
                ),
            },
        )

    candidates = []
    grounded_by_candidate = {}
    try:
        for chunk in chunks:
            for candidate in extract_fn(chunk.text, chunk.chapter):
                candidates.append(candidate)
                grounded_by_candidate[_candidate_key(candidate)] = is_quote_grounded(
                    candidate, chunk.text
                )
    except Exception as exc:
        return (
            candidates,
            grounded_by_candidate,
            {
                "attempted": True,
                "error": True,
                "message": f"Extraction failed: {exc}",
            },
        )
    return (
        candidates,
        grounded_by_candidate,
        {
            "attempted": True,
            "message": (
                f"Extraction ran on {_plural(len(chunks), 'retrieved policy chunk')} and "
                f"produced {_plural(len(candidates), 'draft candidate')}."
            ),
        },
    )


def _candidate_label(candidate: RuleCandidate) -> str:
    return (
        f"{candidate.column_1} / {candidate.column_2} "
        f"CCMI {_ccmi_value(candidate.modifier_indicator)}"
    )


def _candidate_key(candidate: RuleCandidate) -> tuple[str, str, ModifierIndicator]:
    return (candidate.column_1, candidate.column_2, candidate.modifier_indicator)


def _retriever_summary(arm: dict) -> dict[str, str]:
    chunks = arm["chunks"]
    retriever = arm["retriever"]
    if retriever == "direct":
        arm_kind = "control"
        label = "Direct injection (control)"
        description = (
            "Direct means the control retriever: it scans loaded policy manual chapters for "
            "the query code terms and returns matching chapters without embeddings or vector search."
        )
    else:
        arm_kind = "treatment"
        label = "Chroma vector search (treatment)"
        description = (
            "Chroma means the treatment retriever: it embeds chunked policy text and returns "
            "the nearest vector matches."
        )
    return {
        "arm": arm_kind,
        "label": label,
        "description": description,
        "count": _plural(len(chunks), "chunk"),
        "empty_message": (
            "No policy chunks reached extraction from this retriever. "
            f"Try the demo query {DEFAULT_QUERY} or confirm the manual data is present."
        ),
    }


def _chunk_label(chunk) -> str:
    score = "n/a" if chunk.score is None else f"{chunk.score:.4f}"
    return f"{chunk.chapter} | {len(chunk.text):,} chars | score {score}"


def _plural(count: int, noun: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {noun}{suffix}"


def _ccmi_value(indicator: ModifierIndicator) -> int:
    return indicator.value


if __name__ == "__main__":
    main()
