"""Retriever contract for Phase 2 — the Chroma ablation lives behind this seam.

The point of this interface is scientific, not architectural: by making policy
retrieval pluggable, Phase 5 can run extraction through TWO arms and report the
delta, instead of baking Chroma in and never knowing whether it helped.

    DirectInjectionRetriever  -> the CONTROL. Hands the model the relevant chapter
                                 text directly. No embeddings, no retrieval risk.
    ChromaRetriever           -> the TREATMENT. Vector search over chunked policy.

Phase 0 ships the interface and the contract. The bodies are implemented in
Phase 2. Do not implement them here — implement them against the BDD test named
in each docstring.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from contextlib import contextmanager
import json
import logging
import os
import re
from typing import Optional
import urllib.request
import warnings

import chromadb
from chromadb.config import Settings

from pydantic import BaseModel, Field
from pydantic.warnings import PydanticDeprecatedSince211


EmbeddingFn = Callable[[str], list[float]]
_TOKEN = re.compile(r"[A-Za-z0-9]+")
_CODE = re.compile(r"^[A-Z0-9]{5}$")


class PolicyChunk(BaseModel):
    """A retrieved span of the NCCI Policy Manual, with provenance."""

    chapter: str
    section: Optional[str] = None
    text: str = Field(..., min_length=1)
    score: Optional[float] = Field(
        default=None, description="Retriever relevance score; None for direct injection"
    )


class Retriever(ABC):
    """Return the policy text most relevant to a query, newest-best-first.

    Contract every implementation must honor:
      - `retrieve(query, k)` returns at most `k` PolicyChunk objects.
      - Every returned chunk has non-empty `text` and a real `chapter`.
      - The retriever is read-only and deterministic for a fixed corpus + query.
      - `name` is unique per arm and is the value recorded in TrackAResult.
    """

    name: str

    @abstractmethod
    def retrieve(self, query: str, k: int = 5) -> list[PolicyChunk]:
        ...


class DirectInjectionRetriever(Retriever):
    """CONTROL arm. Given a chapter-keyed corpus, return the matching chapter(s).

    BDD test to satisfy (Phase 2):
      Given a corpus where chapter 'Chapter 1' documents code pair 11042/97597,
      when retrieve('11042 97597') is called,
      then a PolicyChunk for 'Chapter 1' is returned.
    """

    name = "direct"

    def __init__(self, corpus: dict[str, str]) -> None:
        # corpus maps chapter label -> chapter text. Loaded in Phase 1.
        self._corpus = corpus

    def retrieve(self, query: str, k: int = 5) -> list[PolicyChunk]:
        terms = _query_terms(query)
        if not terms or k <= 0:
            return []

        matches = []
        for chapter, text in self._corpus.items():
            score = _lexical_score(text, terms)
            if score:
                matches.append((score, chapter, text))
        matches.sort(key=lambda item: (-item[0], item[1]))
        return [
            PolicyChunk(chapter=chapter, text=text, score=None)
            for _, chapter, text in matches[:k]
        ]


class ChromaRetriever(Retriever):
    """TREATMENT arm. Vector search over chunked policy text via ChromaDB.

    BDD test to satisfy (Phase 2):
      Given the manual indexed in Chroma,
      when retrieve(query) is called for a query whose answer lives in chapter N,
      then chapter N appears in the top-k results.

    Keep the embedding model and chunk size as constructor args so the ablation
    can hold them fixed while comparing against the control.
    """

    name = "chroma"

    def __init__(
        self,
        collection_name: str,
        embedding_model: str | None = None,
        *,
        collection=None,
        embedding_fn: EmbeddingFn | None = None,
        chunk_size: int = 1200,
    ) -> None:
        self._embedding_model = embedding_model
        self._collection = collection
        self._embedding_fn = embedding_fn

    def retrieve(self, query: str, k: int = 5) -> list[PolicyChunk]:
        terms = _query_terms(query)
        if self._collection is None:
            raise RuntimeError("Use build_chroma_index to construct an indexed ChromaRetriever")
        if not terms or k <= 0:
            return []

        embedding_fn = self._embedding_fn or _ollama_embedding_fn(self._embedding_model)
        with _chroma_pydantic_compat():
            results = self._collection.query(
                query_embeddings=[embedding_fn(query)],
                n_results=k,
                include=["documents", "metadatas", "distances"],
            )
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        chunks = []
        for document, metadata, distance in zip(documents, metadatas, distances):
            if not document:
                continue
            chunks.append(
                PolicyChunk(
                    chapter=metadata["chapter"],
                    text=document,
                    score=float(distance),
                )
            )
        return chunks


def build_chroma_index(
    corpus: dict[str, str],
    *,
    collection_name: str,
    embedding_fn: EmbeddingFn | None = None,
    chunk_size: int = 1200,
) -> ChromaRetriever:
    embed = embedding_fn or _ollama_embedding_fn()
    _silence_chroma_telemetry()
    client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
    with _chroma_pydantic_compat():
        collection = client.get_or_create_collection(collection_name)

    ids = []
    documents = []
    metadatas = []
    embeddings = []
    for chapter, text in corpus.items():
        for index, chunk in enumerate(_chunks(text, chunk_size)):
            ids.append(f"{chapter}:{index}")
            documents.append(chunk)
            metadatas.append({"chapter": chapter})
            embeddings.append(embed(chunk))

    if documents:
        with _chroma_pydantic_compat():
            collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings,
            )

    return ChromaRetriever(
        collection_name,
        os.environ.get("POLICYFORGE_EMBEDDING_MODEL"),
        collection=collection,
        embedding_fn=embed,
    )


def _query_terms(query: str) -> list[str]:
    tokens = [token.upper() for token in _TOKEN.findall(query)]
    code_terms = [token for token in tokens if _CODE.match(token)]
    if code_terms:
        return code_terms
    return [token for token in tokens if len(token) >= 3]


def _lexical_score(text: str, terms: list[str]) -> int:
    haystack = text.upper()
    if not all(term in haystack for term in terms):
        return 0
    return sum(haystack.count(term) for term in terms)


def _chunks(text: str, chunk_size: int) -> Iterable[str]:
    clean = text.strip()
    if not clean:
        return

    overlap = min(100, max(0, chunk_size // 5))
    step = max(1, chunk_size - overlap)
    for start in range(0, len(clean), step):
        chunk = clean[start : start + chunk_size].strip()
        if chunk:
            yield chunk
        if start + chunk_size >= len(clean):
            break


def _ollama_embedding_fn(model: str | None = None) -> EmbeddingFn:
    base_url = os.environ.get("POLICYFORGE_OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    embedding_model = model or os.environ.get("POLICYFORGE_EMBEDDING_MODEL")
    if not embedding_model:
        raise ValueError("POLICYFORGE_EMBEDDING_MODEL must be set")

    def embed(text: str) -> list[float]:
        body = json.dumps({"model": embedding_model, "input": text}).encode()
        request = urllib.request.Request(
            f"{base_url}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
        return [float(value) for value in payload["embeddings"][0]]

    return embed


def _silence_chroma_telemetry() -> None:
    """Quiet chromadb's broken product-telemetry path.

    chromadb 0.5.x calls ``posthog.capture()`` with three positional args, but
    ``posthog>=7`` accepts one, so every telemetry send fails and logs a (non-fatal)
    ERROR — and the ``anonymized_telemetry=False`` setting is ignored in this version.
    Retrieval is unaffected; this only silences the dependency's broken telemetry
    logger so the demo/console stays clean. The proper long-term fix is pinning a
    compatible ``posthog`` (needs a dependency change), tracked as a follow-up.
    """
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)


@contextmanager
def _chroma_pydantic_compat():
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            category=PydanticDeprecatedSince211,
            module=r"chromadb\.types",
        )
        yield
