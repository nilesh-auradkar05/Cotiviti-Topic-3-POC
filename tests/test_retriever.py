"""Phase 2 retriever tests."""

from __future__ import annotations

from numbers import Real

import pytest

from policyforge import retriever as retriever_module
from policyforge.retriever import (
    ChromaRetriever,
    DirectInjectionRetriever,
    _ollama_embedding_fn,
    build_chroma_index,
)


CORPUS = {
    "Chapter 1": (
        "Chapter I documents that CPT code 11042 is the Column 1 code and CPT code "
        "97597 is the Column 2 code for a PTP edit with modifier indicator 1."
    ),
    "Chapter 10": (
        "Chapter X documents that CPT code 80053 is the Column 1 code and CPT code "
        "80048 is the Column 2 code for a laboratory panel edit."
    ),
    "Chapter 11": (
        "Chapter XI documents that CPT code 93000 is the Column 1 code and CPT code "
        "93005 is the Column 2 code for a cardiovascular edit."
    ),
}


def _fake_embedding(text: str) -> list[float]:
    chapter_11 = "93000" in text or "93005" in text or "cardiac tracing" in text.lower()
    return [
        float(token in text)
        for token in ("11042", "97597", "80053", "80048", "93000", "93005")
    ] + [float(chapter_11)]


def _chroma() -> ChromaRetriever:
    return build_chroma_index(
        CORPUS,
        collection_name="policyforge_test_retriever",
        embedding_fn=_fake_embedding,
        chunk_size=1000,
    )


def test_direct_injection_returns_the_chapter_that_documents_a_known_pair():
    chunks = DirectInjectionRetriever(CORPUS).retrieve("11042 97597")

    assert [chunk.chapter for chunk in chunks] == ["Chapter 1"]
    assert chunks[0].score is None
    assert "11042" in chunks[0].text
    assert "97597" in chunks[0].text


def test_chroma_returns_the_chapter_whose_chunk_answers_the_query():
    chunks = _chroma().retrieve("11042 97597", k=1)

    assert [chunk.chapter for chunk in chunks] == ["Chapter 1"]
    assert isinstance(chunks[0].score, Real)


def test_chroma_returns_semantically_near_chunks_without_a_lexical_match():
    chunks = _chroma().retrieve("cardiac tracing", k=1)

    assert [chunk.chapter for chunk in chunks] == ["Chapter 11"]


def test_an_unindexed_chroma_retriever_does_not_hide_misconfiguration():
    retriever = ChromaRetriever("missing_collection", embedding_fn=_fake_embedding)

    with pytest.raises(RuntimeError, match="build_chroma_index"):
        retriever.retrieve("11042 97597")


def test_an_unindexed_chroma_retriever_does_not_require_embedding_env(monkeypatch):
    monkeypatch.delenv("POLICYFORGE_EMBEDDING_MODEL", raising=False)

    retriever = ChromaRetriever("missing_collection")

    with pytest.raises(RuntimeError, match="build_chroma_index"):
        retriever.retrieve("11042 97597")


def test_both_retriever_arms_cap_results_at_k():
    retrievers = [DirectInjectionRetriever(CORPUS), _chroma()]

    for retriever in retrievers:
        assert len(retriever.retrieve("CPT code", k=2)) == 2
        assert len(retriever.retrieve("CPT code", k=1)) == 1


def test_both_retriever_arms_select_the_chapter_that_documents_a_known_pair():
    retrievers = [DirectInjectionRetriever(CORPUS), _chroma()]

    for retriever in retrievers:
        chunks = retriever.retrieve("93000 93005", k=1)
        assert [chunk.chapter for chunk in chunks] == ["Chapter 11"]


def test_direct_injection_returns_no_chunks_for_a_query_with_no_policy_match():
    assert DirectInjectionRetriever(CORPUS).retrieve("77777 88888") == []


def test_chroma_returns_top_k_for_a_query_with_no_lexical_policy_match():
    chunks = _chroma().retrieve("77777 88888", k=2)

    assert len(chunks) == 2
    assert all(chunk.chapter for chunk in chunks)
    assert all(chunk.text for chunk in chunks)


def test_retrieval_is_deterministic_for_a_fixed_corpus_and_query():
    retrievers = [DirectInjectionRetriever(CORPUS), _chroma()]

    for retriever in retrievers:
        first = retriever.retrieve("80053 80048", k=2)
        second = retriever.retrieve("80053 80048", k=2)
        assert [chunk.chapter for chunk in first] == [chunk.chapter for chunk in second]
        assert [chunk.text for chunk in first] == [chunk.text for chunk in second]


def test_ollama_embedding_uses_the_current_embed_endpoint(monkeypatch):
    requests = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def read(self):
            return b'{"embeddings": [[0.1, 0.2]]}'

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("POLICYFORGE_OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("POLICYFORGE_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setattr(retriever_module.urllib.request, "urlopen", fake_urlopen)

    embedding = _ollama_embedding_fn()("11042 97597")

    request, timeout = requests[0]
    assert request.full_url == "http://localhost:11434/api/embed"
    assert request.data == b'{"model": "nomic-embed-text", "input": "11042 97597"}'
    assert timeout == 30
    assert embedding == [0.1, 0.2]
