"""Phase 7 orchestration graph for the PolicyForge demo."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import TypedDict
import warnings

from policyforge.engine import adjudicate
from policyforge.extraction import is_quote_grounded
from policyforge.gate import gate_node
from policyforge.schemas import Claim, ClaimDisposition, PTPRule, RuleCandidate
from policyforge.store import RuleStore


class DemoState(TypedDict, total=False):
    query: str
    ruleset_version: str
    authoritative_rules: list[PTPRule]
    retrieved_chunks: list
    retrieval_trace: list[dict]
    candidates: list[RuleCandidate]
    candidate: RuleCandidate
    quote_grounded: bool
    approver: str
    approved_at: datetime
    approved_rule_id: str | None
    gate_decision: str
    claim: Claim
    disposition: ClaimDisposition
    authoritative_seeded: bool


ExtractFn = Callable[[str, str], list[RuleCandidate]]
Clock = Callable[[], datetime]
_ROOT = Path(__file__).resolve().parents[1]
_PTP_TABLE_DIR = _ROOT / "data" / "ccipra-v322r0-f1"
_POLICY_MANUAL_PATH = _ROOT / "data" / "2026_ncci_medicare_policy_manual_all-chapters.pdf"


def build_demo_graph(*, retrievers, extract_fn, store, checkpointer, clock):
    StateGraph, START, END = _langgraph_graph()

    def ingest(state: DemoState) -> dict:
        if not state.get("authoritative_seeded"):
            rules = state.get("authoritative_rules", [])
            if rules:
                store.seed_authoritative(rules, ruleset_version=state["ruleset_version"])
        return {"authoritative_seeded": True}

    def retrieve(state: DemoState) -> dict:
        all_chunks = []
        trace = []
        for retriever in retrievers:
            chunks = retriever.retrieve(state["query"], k=5)
            all_chunks.extend(chunks)
            trace.append(
                {
                    "retriever_name": retriever.name,
                    "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
                }
            )
        return {"retrieved_chunks": all_chunks, "retrieval_trace": trace}

    def extract(state: DemoState) -> dict:
        candidates = []
        grounded_by_key = {}
        for chunk in state.get("retrieved_chunks", []):
            for candidate in extract_fn(chunk.text, chunk.chapter):
                candidates.append(candidate)
                key = (candidate.column_1, candidate.column_2, candidate.modifier_indicator)
                grounded_by_key.setdefault(key, is_quote_grounded(candidate, chunk.text))
        if not candidates:
            return {"candidates": []}
        first = candidates[0]
        first_key = (first.column_1, first.column_2, first.modifier_indicator)
        return {
            "candidates": candidates,
            "candidate": first,
            "quote_grounded": grounded_by_key[first_key],
        }

    def gate(state: DemoState, config=None) -> dict:
        gate_state = dict(state)
        gate_state.setdefault("approver", "demo-reviewer")
        gate_state.setdefault("approved_at", clock())
        gate_config = {"configurable": {**(config or {}).get("configurable", {}), "store": store}}
        return gate_node(gate_state, gate_config)

    def adjudicate_node(state: DemoState) -> dict:
        rules = store.load_ruleset(state["ruleset_version"])
        disposition = adjudicate(state["claim"], rules, state["ruleset_version"])
        return {"disposition": disposition}

    builder = StateGraph(DemoState)
    builder.add_node("ingest", ingest)
    builder.add_node("retrieve", retrieve)
    builder.add_node("extract", extract)
    builder.add_node("gate", gate)
    builder.add_node("adjudicate", adjudicate_node)
    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "retrieve")
    builder.add_edge("retrieve", "extract")
    builder.add_edge("extract", "gate")
    builder.add_edge("gate", "adjudicate")
    builder.add_edge("adjudicate", END)
    return builder.compile(checkpointer=checkpointer)


def run_demo() -> None:
    InMemorySaver = _memory_saver()
    from policyforge.extraction import extract_rules
    from policyforge.ingestion import load_policy_sections, load_ptp_table
    from policyforge.retriever import DirectInjectionRetriever, build_chroma_index

    ruleset_version = os.environ.get("POLICYFORGE_RULESET_VERSION", "demo")
    corpus = load_policy_sections(_POLICY_MANUAL_PATH)
    retrievers = [DirectInjectionRetriever(corpus)]
    if os.environ.get("POLICYFORGE_EMBEDDING_MODEL"):
        retrievers.append(
            build_chroma_index(
                corpus,
                collection_name="policyforge-demo",
            )
        )
    store = RuleStore(os.environ.get("POLICYFORGE_STORE_PATH", "data/policyforge_store.db"))
    if ruleset_version not in store.versions():
        store.seed_authoritative(load_ptp_table(_PTP_TABLE_DIR), ruleset_version=ruleset_version)
    graph = build_demo_graph(
        retrievers=retrievers,
        extract_fn=extract_rules,
        store=store,
        checkpointer=InMemorySaver(),
        clock=lambda: datetime.now(timezone.utc),
    )
    graph.get_graph()


def _memory_saver():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver


def _langgraph_graph():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from langgraph.graph import END, START, StateGraph

    return StateGraph, START, END
