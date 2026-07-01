# PolicyForge

**Converting written health-care policy into auditable, executable claim edits.**

PolicyForge is a proof of concept for Cotiviti Intern Assessment Topic 3 (Content
Management in Health Care). It turns CMS NCCI policy prose into deterministic,
citeable payment edits — without ever letting a language model make a payment
decision.

## The seam

The whole design rests on one boundary — *the seam*:

```
policy text  --[ LLM converts ]-->  draft rule candidate
                                          |
                                   [ human gate ]  (approve / correct / reject)
                                          |
                                   versioned rule store  (provenance recorded)
                                          |
                              [ deterministic engine decides ]
                                          |
                            cited disposition  (rule id + ruleset version)
```

The LLM may **convert** prose into a *draft*. It may never **decide** a claim.
Decisions run on a deterministic engine (`policyforge/engine.py`) with no model,
embedding, or randomness on the path, and every deny/flag cites the exact rule
and ruleset version that produced it.

## What's in the box

| Module | Role |
| --- | --- |
| `schemas.py` | Pydantic contracts — the single source of truth for every phase |
| `ingestion.py` | Load CMS PTP table + split the policy manual into chapters |
| `retriever.py` | Direct lexical retrieval (control) + Chroma vector retrieval (ablation) |
| `extraction.py` | LLM drafts `RuleCandidate`s from prose (grounded to a source quote) |
| `gate.py` | Human-in-the-loop LangGraph interrupt: approve / correct CCMI / reject |
| `store.py` | SQLite versioned rule store with full provenance |
| `engine.py` | Deterministic NCCI PTP adjudication (the decision path) |
| `evaluation/run_eval.py` | Track A extraction fidelity + Track B adjudication correctness |
| `app/main.py` | Streamlit demo shell (Pipeline / Gate / Adjudicate) |

## Quick start

```bash
make install      # editable install + dev tools
make test         # 100+ tests, fully offline (no network / API / Ollama)
make lint         # ruff
```

`make test` and `make lint` need **no** data, API key, or services.

### Run the demo

```bash
make demo         # streamlit run app/main.py  ->  http://localhost:8501
```

- **With no data and no API key:** the **Adjudicate** tab still works — it seeds the
  canonical demo pair `11042 / 97597` from the bundled `fixtures/sample_ptp.csv` and
  shows the deterministic decision + provenance. This is the seam, fully offline.
- **With the CMS manual under `data/` and an `ANTHROPIC_API_KEY`:** the **Pipeline**
  tab retrieves real chapters and runs live extraction, and the **Gate** tab lets a
  reviewer approve, correct the CCMI, or reject each draft.

### Optional live paths

Put these in a repo-root `.env` (loaded automatically by `make demo`):

```text
ANTHROPIC_API_KEY=...
POLICYFORGE_EXTRACTION_MODEL=claude-sonnet-4-6
POLICYFORGE_EMBEDDING_MODEL=nomic-embed-text     # enables the Chroma ablation arm
POLICYFORGE_OLLAMA_BASE_URL=http://localhost:11434
```

CMS/CPT source data is licensed and is **not** committed. `make fetch-data` prints the
official CMS download locations; extract them under `data/`.

## Evaluation, honestly

Two tracks, reported separately on purpose:

- **Track A — extraction fidelity.** LLM candidates scored against the authoritative
  PTP table. Read recall over the **extractable** denominator (gold pairs that are
  actually stated as explicit code pairs in the prose) — recall over the full table
  is low *by construction*, because the manual states most guidance as principles,
  not code pairs. `make eval` prints both numbers.
- **Track B — adjudication correctness.** Synthetic claims scored against the
  deterministic engine. This is the un-fakeable north star and should be ~100%.

The point of the demo is to show the seam, the human gate, and the provenance
honestly — not to manufacture a perfect extraction score.

## Design docs

See `SPEC.md` (scope, phase plan, definitions of done) and `DECISIONS.md` for the
engineering rationale.
