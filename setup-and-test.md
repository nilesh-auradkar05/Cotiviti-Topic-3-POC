# PolicyForge Setup and Test Guide

This guide shows how to set up the project, run each testable feature, and verify
the Phase 7 demo manually.

## 1. Environment Setup

1. Create and activate a Python environment with Python 3.11 or newer.

2. Install the package and dev tools.

   ```bash
   make install
   ```

3. Confirm the CMS data is present.

   Required files:

   ```text
   data/2026_ncci_medicare_policy_manual_all-chapters.pdf
   data/ccipra-v322r0-f1/
   data/ccipra-v322r0-f1/ncci_ptp_goldset_100.xlsx
   ```

   If data is missing, run:

   ```bash
   make fetch-data
   ```

   That target prints the CMS download locations. Download/extract the files into
   `data/`.

4. Optional environment variables for live model-backed paths. You can export
   them in your shell or put them in a repo-root `.env` file; `make demo` loads
   `.env` automatically.

   ```bash
   export ANTHROPIC_API_KEY=...
   export POLICYFORGE_EXTRACTION_MODEL=claude-sonnet-4-6
   export POLICYFORGE_OLLAMA_BASE_URL=http://localhost:11434
   export POLICYFORGE_EMBEDDING_MODEL=nomic-embed-text
   export POLICYFORGE_STORE_PATH=data/policyforge_store.db
   ```

   Equivalent `.env` example:

   ```text
   ANTHROPIC_API_KEY=...
   POLICYFORGE_EXTRACTION_MODEL=claude-sonnet-4-6
   POLICYFORGE_OLLAMA_BASE_URL=http://localhost:11434
   POLICYFORGE_EMBEDDING_MODEL=nomic-embed-text
   POLICYFORGE_STORE_PATH=data/policyforge_store.db
   ```

   `make test` does not require network, Anthropic, Ollama, or Chroma services.

## 2. Required Project Checks

Run the full automated gate:

```bash
make test
make lint
```

Expected output:

```text
pytest
... passed ...

ruff check policyforge tests
All checks passed!
```

## 3. Feature-by-Feature Tests

### Phase 1: Ingestion

```bash
pytest tests/test_ingestion.py -q
```

Expected behavior:

- CMS PTP rows load into validated `PTPRule` objects.
- The known pair `PTP:11042:97597` is present.
- Invalid CCMI values, malformed codes, and header drift fail through schema validation.

### Phase 2: Retriever Arms

```bash
pytest tests/test_retriever.py -q
```

Expected behavior:

- Direct retrieval returns matching policy chapters by code terms.
- Chroma retrieval is tested with deterministic fake embeddings.
- Tests make no network call.

In the demo UI, `Direct injection (control)` means the control retriever scans the
loaded manual chapters for the query code terms and returns matching chapters
without embeddings or vector search.

### Phase 3: LLM Extraction

```bash
pytest tests/test_extraction.py -q
```

Expected behavior:

- Canned Anthropic tool output is parsed into `RuleCandidate(...)`.
- Ungrounded candidates are recorded rather than silently dropped.
- Malformed candidates raise validation errors.

Live extraction in the demo requires `ANTHROPIC_API_KEY`. If the key is not set,
the Pipeline tab retrieves policy text but skips extraction with an explicit
message. If the key is set and the model returns no explicit code-pair
candidates, the Gate tab will be empty by design.

### Phase 4: Deterministic Engine

```bash
pytest tests/test_engine.py -q
```

Expected behavior:

- Same-date-of-service PTP pairs adjudicate deterministically.
- CCMI `0` denies Column 2.
- CCMI `1` denies Column 2 without an NCCI modifier and flags Column 2 with a
  valid modifier such as `59`.
- Every deny/flag line cites `cited_rule_id`, and every disposition carries
  `ruleset_version`.

### Phase 5: Eval

```bash
pytest tests/test_eval.py -q
```

Expected behavior:

- Track A scores extraction candidates against authoritative PTP rules.
- Track B is deterministic exact-match adjudication scoring.
- UNCERTAIN cases are excluded and reported separately.

For the real eval:

```bash
make eval
```

Expected output includes Track A and Track B metrics. This path needs the gold set
under `data/ccipra-v322r0-f1/ncci_ptp_goldset_100.xlsx`.

### Phase 6: Gate and Store

```bash
pytest tests/test_gate.py -q
```

Expected behavior:

- Rejected candidates never reach the store.
- Approved candidates are constructed through `PTPRule(...)` and persisted.
- Invalid approvals raise validation errors and write nothing.
- `load_ruleset()` reconstructs validated `PTPRule` objects.

### Phase 7: Orchestration and Demo Shell

```bash
pytest tests/test_orchestration.py -q
```

Expected behavior:

- The graph path is `ingest -> retrieve -> extract -> gate -> adjudicate`.
- Approval path yields a cited disposition from an approved rule.
- Reject path leaves the store empty and the claim pays.
- The graph interrupts before adjudication while a candidate is pending.
- The adjudication node stays model-free.

## 4. Manual Streamlit Demo

Start the UI:

```bash
make demo
```

If your shell finds a user-level Streamlit instead of the project environment,
run:

```bash
PATH=.venv/bin:$PATH make demo
```

Open the local URL printed by Streamlit. If port `8501` is busy, Streamlit may use
the next available port.

### Pipeline Tab

1. Leave the default query as:

   ```text
   11042 97597
   ```

2. Leave `Treatment retrieval` unchecked unless Ollama and
   `POLICYFORGE_EMBEDDING_MODEL` are configured.

3. Click `Run pipeline`.

Expected output:

- A `Direct injection (control)` section.
- Text explaining that direct retrieval is the control arm and uses no embeddings.
- A retrieved chunk count. With the current real manual, the default query should
  return policy chunks such as `Chapter 11`, `Chapter 3`, and `Chapter 4`.
- Expanders for each retrieved chapter showing the policy text.
- An `Extraction` section.

If `ANTHROPIC_API_KEY` is not configured, expected output is:

```text
Extraction skipped because ANTHROPIC_API_KEY is not configured.
```

This is still a useful retrieval check. It proves the manual was ingested and the
control retriever found policy text, but no draft rules are available for Gate
review.

If extraction produces candidates, expect a table with:

- `column_1`
- `column_2`
- `ccmi`
- `chapter`
- `confidence`
- `grounded`
- `source_quote`

If Anthropic is configured and extraction produces zero candidates, expect:

```text
Extraction ran on ... retrieved policy chunk(s) and produced 0 draft candidates.
```

That is not a UI failure. It means no draft `RuleCandidate` is available for
human review from that pipeline run.

### Gate Tab

If Pipeline produced no candidates, expected output is:

```text
Pipeline ran, but no draft RuleCandidate objects were extracted for review.
```

If Pipeline produced candidates:

1. Select a candidate.
2. Review the JSON payload.
3. Set `Effective date`.
4. Click `Approve` to create and store a `PTPRule`, or `Reject` to discard it.

Expected approve output:

```text
Approved PTP:<column_1>:<column_2>
```

Rejected candidates do not enter the store and cannot affect adjudication.

### Adjudicate Tab

The app seeds the canonical demo pair from the authoritative CMS table if it is
not already present:

```text
PTP:11042:97597
```

Default claim:

```text
Line 1 code: 11042
Line 2 code: 97597
Line 2 modifier: empty
Date of service: 2026/01/01
```

Click `Adjudicate`.

Expected output:

- Line 1 `status` is `pay`.
- Line 2 `status` is `deny`.
- Line 2 `reason_code` is `CO-97`.
- Line 2 `cited_rule_id` is `PTP:11042:97597`.
- `ruleset_version` is the sidebar ruleset value, default `demo`.
- A provenance expander appears for `PTP:11042:97597`.

To test the CCMI-1 modifier path:

1. Set `Line 2 modifier` to `59`.
2. Click `Adjudicate`.

Expected output:

- Line 1 `status` is `pay`.
- Line 2 `status` is `flag`.
- Line 2 cites `PTP:11042:97597`.

To test date applicability:

1. Set `Date of service` before `2005/01/01`.
2. Click `Adjudicate`.

Expected output:

- Both lines pay because the authoritative demo rule is not active yet.

## 5. Troubleshooting

- Gate is empty after Pipeline:
  Pipeline did not produce `RuleCandidate` objects. Check whether retrieval
  returned chunks and whether `ANTHROPIC_API_KEY` is configured for live
  extraction.

- Pipeline only says zero chunks:
  Use the default query `11042 97597`. The previous `93000 93005` query does not
  match a real authoritative pair in the bundled CMS file.

- Adjudicate returns all `pay`:
  Confirm the claim uses `11042` and `97597`, date of service is on or after
  `2005/01/01`, and the ruleset caption shows at least one rule.

- Streamlit cannot import `streamlit`:
  Run `PATH=.venv/bin:$PATH make demo` so the project virtualenv is first.

- Streamlit keeps old behavior after a code change:
  Stop the server with `Ctrl-C` and restart `make demo`.
