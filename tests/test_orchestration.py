"""Phase 7 orchestration graph tests."""

from __future__ import annotations

import builtins
from datetime import date, datetime, timezone
import importlib.util
import os
from pathlib import Path
import threading
from typing import Any
import warnings

import pytest

from policyforge.orchestration import build_demo_graph
from policyforge.retriever import PolicyChunk
from policyforge.schemas import (
    Claim,
    ClaimDisposition,
    ClaimLine,
    DispositionStatus,
    ModifierIndicator,
    PTPRule,
    RuleCandidate,
)
from policyforge.store import RuleStore


RULESET_VERSION = "demo-rules-v1"
APPROVED_AT = datetime(2026, 7, 1, 13, 0, tzinfo=timezone.utc)
QUOTE = "CPT code 93000 is the Column 1 code and CPT code 93005 is the Column 2 code."
CHAPTER_TEXT = f"{QUOTE} The modifier indicator is 0."

warnings.filterwarnings(
    "ignore",
    message=r"The default value of `allowed_objects` will change.*",
    category=Warning,
)
pytestmark = pytest.mark.filterwarnings(
    "ignore:The default value of `allowed_objects` will change.*:"
    "langchain_core._api.deprecation.LangChainPendingDeprecationWarning"
)


class FakeRetriever:
    def __init__(self, chunks: list[PolicyChunk], name: str = "direct") -> None:
        self._chunks = chunks
        self.name = name

    def retrieve(self, query: str, k: int = 5) -> list[PolicyChunk]:
        return self._chunks[:k]


def _candidate() -> RuleCandidate:
    return RuleCandidate(
        column_1="93000",
        column_2="93005",
        modifier_indicator=ModifierIndicator.NOT_ALLOWED,
        rationale="Misuse of column two code with column one code",
        source_chapter="Chapter 11",
        source_quote=QUOTE,
        extraction_confidence=0.91,
    )


def _approved_rule() -> PTPRule:
    return PTPRule(
        column_1="93000",
        column_2="93005",
        modifier_indicator=ModifierIndicator.NOT_ALLOWED,
        effective_date=date(2020, 1, 1),
        rationale="Misuse of column two code with column one code",
    )


def _canonical_demo_rule() -> PTPRule:
    return PTPRule(
        column_1="11042",
        column_2="97597",
        modifier_indicator=ModifierIndicator.ALLOWED,
        effective_date=date(2005, 1, 1),
        rationale="CPT Manual or CMS manual coding instruction",
    )


def _extract_fn(text: str, source_chapter: str) -> list[RuleCandidate]:
    candidate = _candidate()
    return [candidate.model_copy(update={"source_chapter": source_chapter})]


def _claim() -> Claim:
    return Claim(
        claim_id="C1",
        beneficiary_id="B1",
        provider_id="P1",
        lines=[
            ClaimLine(line_id="L1", code="93000", date_of_service=date(2026, 7, 1)),
            ClaimLine(line_id="L2", code="93005", date_of_service=date(2026, 7, 1)),
        ],
    )


def _build_graph(store: RuleStore):
    return build_demo_graph(
        retrievers=[FakeRetriever([PolicyChunk(chapter="Chapter 11", text=CHAPTER_TEXT)])],
        extract_fn=_extract_fn,
        store=store,
        checkpointer=_checkpointer(),
        clock=lambda: APPROVED_AT,
    )


def _initial_state() -> dict[str, Any]:
    return {
        "query": "93000 93005",
        "claim": _claim(),
        "ruleset_version": RULESET_VERSION,
        "authoritative_rules": [],
        "approver": "ncci-reviewer",
    }


def test_demo_graph_builds_the_phase_seven_node_order():
    graph = _build_graph(RuleStore(":memory:"))
    graph_view = graph.get_graph()
    edges = {(edge.source, edge.target) for edge in graph_view.edges}
    expected_path = [
        ("__start__", "ingest"),
        ("ingest", "retrieve"),
        ("retrieve", "extract"),
        ("extract", "gate"),
        ("gate", "adjudicate"),
        ("adjudicate", "__end__"),
    ]

    assert set(graph_view.nodes) >= {"ingest", "retrieve", "extract", "gate", "adjudicate"}
    assert edges == set(expected_path)


def test_retrieve_records_both_arms_of_the_direct_vs_chroma_ablation():
    direct_chunks = [PolicyChunk(chapter="Chapter 11", text=CHAPTER_TEXT)]
    chroma_chunks = [
        PolicyChunk(chapter="Chapter 1", text="Vector-matched policy text.", score=0.42)
    ]
    graph = build_demo_graph(
        retrievers=[
            FakeRetriever(direct_chunks, name="direct"),
            FakeRetriever(chroma_chunks, name="chroma"),
        ],
        extract_fn=_extract_fn,
        store=RuleStore(":memory:"),
        checkpointer=_checkpointer(),
        clock=lambda: APPROVED_AT,
    )
    config = {"configurable": {"thread_id": "ablation"}}

    list(graph.stream(_initial_state(), config))

    trace = graph.get_state(config).values["retrieval_trace"]
    by_name = {arm["retriever_name"]: arm for arm in trace}
    assert set(by_name) == {"direct", "chroma"}
    assert by_name["direct"]["chunks"][0]["text"] == CHAPTER_TEXT
    assert by_name["chroma"]["chunks"][0]["text"] == "Vector-matched policy text."
    assert by_name["chroma"]["chunks"][0]["score"] == 0.42


def test_pipeline_runs_end_to_end_approve_path_through_the_gate():
    store = RuleStore(":memory:")
    graph = _build_graph(store)
    config = {"configurable": {"thread_id": "approve-path"}}

    first_chunks = list(graph.stream(_initial_state(), config))
    assert "__interrupt__" in first_chunks[-1]
    assert store.load_ruleset(RULESET_VERSION) == []

    list(
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
    disposition = graph.get_state(config).values["disposition"]

    line_two = _by_line(disposition)["L2"]
    assert line_two.status is DispositionStatus.DENY
    assert line_two.reason_code == "CO-97"
    assert line_two.cited_rule_id == "PTP:93000:93005"
    assert disposition.ruleset_version == RULESET_VERSION


def test_reject_gates_the_pipeline_so_the_claim_pays():
    store = RuleStore(":memory:")
    graph = _build_graph(store)
    config = {"configurable": {"thread_id": "reject-path"}}

    list(graph.stream(_initial_state(), config))
    list(graph.stream(_command({"decision": "reject"}), config))
    disposition = graph.get_state(config).values["disposition"]

    assert store.load_ruleset(RULESET_VERSION) == []
    assert {line.status for line in disposition.line_dispositions} == {DispositionStatus.PAY}


def test_graph_interrupts_before_adjudicating_an_ungated_candidate():
    store = RuleStore(":memory:")
    graph = _build_graph(store)

    chunks = list(graph.stream(_initial_state(), {"configurable": {"thread_id": "interrupt-first"}}))

    assert "__interrupt__" in chunks[-1]
    assert all("adjudicate" not in chunk for chunk in chunks)
    assert graph.get_state({"configurable": {"thread_id": "interrupt-first"}}).values.get(
        "disposition"
    ) is None


def test_adjudication_leg_is_deterministic_through_the_graph():
    first = _approved_disposition("deterministic-a")
    second = _approved_disposition("deterministic-b")

    assert _decision_tuples(first) == _decision_tuples(second)


def test_adjudicate_node_is_model_free(monkeypatch):
    store = RuleStore(":memory:")
    graph = _build_graph(store)
    config = {"configurable": {"thread_id": "model-free"}}
    original_import = builtins.__import__

    list(graph.stream(_initial_state(), config))

    def fail_on_model_import(name, *args, **kwargs):
        if name in {"anthropic", "chromadb"}:
            raise AssertionError(f"adjudicate node imported {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_on_model_import)

    list(
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
    disposition = graph.get_state(config).values["disposition"]
    assert _by_line(disposition)["L2"].status is DispositionStatus.DENY


def test_demo_store_factory_uses_a_fresh_sqlite_connection_per_streamlit_thread(
    tmp_path, monkeypatch
):
    demo_app = _demo_app_module()

    store_path = tmp_path / "demo.db"
    monkeypatch.setenv("POLICYFORGE_STORE_PATH", str(store_path))

    demo_app._store().seed_authoritative([_approved_rule()], ruleset_version=RULESET_VERSION)
    loaded = []
    errors = []

    def load_from_streamlit_worker() -> None:
        try:
            loaded.extend(demo_app._store().load_ruleset(RULESET_VERSION))
        except Exception as exc:  # pragma: no cover - assertion reports the failure
            errors.append(exc)

    worker = threading.Thread(target=load_from_streamlit_worker)
    worker.start()
    worker.join()

    assert errors == []
    assert [rule.rule_id for rule in loaded] == ["PTP:93000:93005"]


def test_demo_defaults_use_the_canonical_ccmi_one_pair():
    demo_app = _demo_app_module()

    assert demo_app.DEFAULT_QUERY == "11042 97597"
    assert demo_app.DEFAULT_LINE_1_CODE == "11042"
    assert demo_app.DEFAULT_LINE_2_CODE == "97597"


def test_pipeline_trace_classifies_direct_as_the_control_arm():
    demo_app = _demo_app_module()
    direct = demo_app._retriever_summary(
        {
            "retriever": "direct",
            "chunks": [PolicyChunk(chapter="Chapter 11", text=CHAPTER_TEXT)],
        }
    )
    treatment = demo_app._retriever_summary({"retriever": "chroma", "chunks": []})

    assert direct["arm"] == "control"
    assert treatment["arm"] == "treatment"
    assert direct["count"] == "1 chunk"


def test_pipeline_trace_reports_when_no_chunks_reach_extraction():
    demo_app = _demo_app_module()
    summary = demo_app._retriever_summary({"retriever": "direct", "chunks": []})

    assert summary["count"] == "0 chunks"
    assert summary["empty_message"]


def test_pipeline_skips_live_extraction_when_anthropic_is_not_configured():
    demo_app = _demo_app_module()

    def fail_if_called(text, chapter):
        raise AssertionError("live extraction should not run without credentials")

    candidates, grounded, status = demo_app._extract_candidates(
        [PolicyChunk(chapter="Chapter 11", text=CHAPTER_TEXT)],
        fail_if_called,
        extraction_enabled=False,
    )

    assert candidates == []
    assert grounded == {}
    assert status["attempted"] is False
    assert "ANTHROPIC_API_KEY" in status["message"]


def test_demo_loads_anthropic_key_from_dotenv_without_overriding_existing_env(
    tmp_path, monkeypatch
):
    demo_app = _demo_app_module()
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "# local demo credentials",
                "ANTHROPIC_API_KEY='from-dotenv'",
                "POLICYFORGE_EXTRACTION_MODEL=demo-model",
            ]
        )
    )

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("POLICYFORGE_EXTRACTION_MODEL", "already-set")

    demo_app._load_dotenv(dotenv)

    assert os.environ["ANTHROPIC_API_KEY"] == "from-dotenv"
    assert os.environ["POLICYFORGE_EXTRACTION_MODEL"] == "already-set"


def test_demo_seed_only_loads_the_authoritative_ruleset_once(tmp_path, monkeypatch):
    demo_app = _demo_app_module()
    store = RuleStore(tmp_path / "demo.db")
    calls = []

    def fake_rules():
        calls.append("loaded")
        return [_canonical_demo_rule()]

    monkeypatch.setattr(demo_app, "_ptp_rules", fake_rules)

    assert demo_app._ensure_authoritative_seeded(store, RULESET_VERSION) == 1
    assert demo_app._ensure_authoritative_seeded(store, RULESET_VERSION) == 0
    assert calls == ["loaded"]
    assert [rule.rule_id for rule in store.load_ruleset(RULESET_VERSION)] == ["PTP:11042:97597"]


def _demo_app_module():
    app_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
    spec = importlib.util.spec_from_file_location("policyforge_demo_app", app_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _approved_disposition(thread_id: str) -> ClaimDisposition:
    graph = _build_graph(RuleStore(":memory:"))
    config = {"configurable": {"thread_id": thread_id}}
    list(graph.stream(_initial_state(), config))
    list(
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
    return graph.get_state(config).values["disposition"]


def _by_line(disposition: ClaimDisposition):
    return {line.line_id: line for line in disposition.line_dispositions}


def _decision_tuples(disposition: ClaimDisposition):
    return [
        (line.line_id, line.status, line.reason_code, line.cited_rule_id)
        for line in disposition.line_dispositions
    ]


def _checkpointer():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


def _command(resume: dict[str, Any]):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from langgraph.types import Command

    return Command(resume=resume)
