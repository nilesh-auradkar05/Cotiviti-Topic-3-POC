# PLAN.md — PolicyForge

> Phase-by-phase plan. Coder: follow AGENTS.md (build only the named phase, import the
> contract, honor the seam, tests-first behavioral). Reviewers: CLAUDE.md §5–6 + SPEC §7 rubric.
>
> - **Phase 1 — Ingestion** (§§1–7): implemented & **signed off** (review outcome in §7).
> - **Phase 4 — Deterministic Engine**: implemented & **signed off** (2026-06-30) — dup-pair fix (ADR-011) + tie-break test fix landed; scoped re-review **GREEN-with-nits** (one tracked LOW nit in DECISIONS "Open / pending"); **Track B unlocked**. Sign-off in the re-review outcome below.
> - **Phase 2 — Retriever arms**: implemented & **signed off** (review outcome at bottom).
> - **Phase 3 — LLM Extraction**: implemented & **signed off** (review outcome at bottom).
> - **Phase 5 — Eval (Tracks A & B)**: implemented & **signed off** (2026-06-30) — code fixes + test additions landed; scoped re-review **GREEN-with-nits** (Track B **105/105** real, seam clean, all 6 changed tests mutation-verified behavioral). Sign-off in the review outcome below.
> - **Phase 6 — Gate + store**: implemented & **signed off** (2026-06-30) — two-reviewer pass **APPROVE-WITH-NITS**, zero blockers; audit-timestamp + DoD-test-bite fixes landed; seam clean, construction-is-the-gate honored, no autonomous-denial path. Sign-off in the review outcome below. Locked decisions: SQLite sidecar-column store (no schema change); gate actions **approve / reject** (**edit-and-accept** kept as a documented future advancement).
> - **Phase 7 — Orchestrate + UI**: implemented & **signed off** (2026-06-30) — two-reviewer pass **APPROVE-WITH-NITS**, zero blockers; direct-vs-chroma ablation test added; the UI crosses no seam and the `adjudicate` node is model-free. Sign-off in the review outcome below. Locked decisions: 3 Streamlit views = Pipeline / Gate / Adjudicate; demo pair **11042/97597** (CCMI-1); the Streamlit shell is not unit-tested (coverage via the graph + library helpers).

## 1. Objective

Turn the published CMS NCCI **Practitioner** PTP edit table into `list[PTPRule]` and the
NCCI **Policy Manual** prose into a chapter-keyed `dict[str, str]`, deterministically and
with no model on the path — the authoritative ground truth every later phase reads.

## 2. Interfaces

New module: `policyforge/ingestion.py`. Both functions return contract types from
`policyforge.schemas` only — **no new schema fields, no parallel shapes** (AGENTS.md §2).

```python
def load_ptp_table(path: str | Path) -> list[PTPRule]: ...
def load_policy_sections(path: str | Path) -> dict[str, str]: ...
```

- `load_ptp_table` — reads the Practitioner PTP file(s) and returns one `PTPRule` per data
  row. `path` is a single table file **or** a directory of segment files (CMS publishes the
  Practitioner table as multiple segments; see Step 1 / Open Questions). Each row is mapped to
  `PTPRule(column_1, column_2, modifier_indicator, effective_date, deletion_date,
  rationale, in_existence_prior_1996)`. Construction goes **through `PTPRule`** so the
  schema's validators (code shape, self-pair, date order) are the gate — the loader does not
  re-validate and does not swallow `ValidationError`.
- `load_policy_sections` — extracts the manual's prose into `{chapter_label: chapter_text}`
  using `pdfplumber` (already a dependency). Pure text extraction, keyed by chapter; **no
  LLM, no chunking, no embedding** (that is Phase 2's job, behind the retriever seam). The
  returned dict is exactly what `DirectInjectionRetriever(corpus=...)` consumes in Phase 2.

Mapping layer (the crux): the loader holds an **explicit** real-header → schema-field map.
The fixture's current headers are snake_case that happens to match the schema field names;
the real CMS headers almost certainly differ. Step 1 reconciles the two and the map is
written/fixed against the real headers, not assumed.

Field-mapping rules the loader must apply (verify each against real data in Step 1):
- `in_existence_prior_1996`: `*` → `True`, blank → `False`.
- `effective_date` / `deletion_date`: parse the real on-disk date format to `date`.
- `deletion_date`: blank / active-edit sentinel → `None` (None means "currently active").
- `modifier_indicator`: integer `0|1|9` → `ModifierIndicator`.

## 3. Steps (each with its verify line)

1. **Reconcile real headers before writing a loader.** Download the real CMS Q3 2026
   Practitioner PTP file **and** the NCCI Policy Manual (see `make fetch-data` for URLs),
   place them under `data/` (gitignored, never committed — SPEC §10). Write a throwaway
   inspection snippet that prints `df.columns` and `df.head()` for the PTP file and the page
   count + first page text of the manual. Compare the real PTP headers, date format, and the
   deletion-date "active" convention against `fixtures/sample_ptp.csv`. **If they differ, fix
   the fixture and the loader's header map together in this same phase** — do not assume the
   sample headers are exact.
   → **verify:** the reconciliation notes (real headers + date format + active-edit
   convention) are recorded in the PR description, and `fixtures/sample_ptp.csv` mirrors the
   real 7-column layout (header strings, date format, `*` marker, blank-deletion convention).

2. **Implement `load_ptp_table`** mapping each data row through `PTPRule`.
   → **verify:** `pytest tests/test_ingestion.py -k ptp` green (scenarios in §4).

3. **Implement `load_policy_sections`** extracting chapter text via `pdfplumber`.
   → **verify:** `pytest tests/test_ingestion.py -k policy` green (scenarios in §4).

4. **Seam + hygiene check.** No Anthropic / Chroma / embedding / randomness import in
   `policyforge/ingestion.py`.
   → **verify:** `grep -nE "anthropic|chromadb|embedding|random" policyforge/ingestion.py`
   returns nothing, and `make lint` (`ruff check`) is clean.

5. **Phase gate.**
   → **verify:** `make test` green.

## 4. Behavioral test expectations (Codex turns each sentence into a named test)

Mirror `tests/test_schemas.py`: each test name is a policy scenario; each assertion encodes a
rule a domain expert would recognize and would survive a from-scratch rewrite of the loader.
Run against `fixtures/sample_ptp.csv` (and a tiny malformed fixture for the negative case).
**No smoke** — "it ran / returned non-null" does not count (SPEC §7, AGENTS.md §4).

PTP table (`load_ptp_table`):
- An active pair loads with its CCMI — `PTP:11042:97597` loads with
  `modifier_indicator == ModifierIndicator.ALLOWED` and `deletion_date is None`.
- A row past its deletion date loads with `deletion_date` set — `PTP:36415:36416` loads with
  `deletion_date == date(2023, 12, 31)` and `is_active_on(date(2026, 7, 1)) is False`.
- A blank deletion date loads as an active edit — `PTP:93000:93005` has `deletion_date is None`.
- A YYYYMMDD effective date parses to a real date — `PTP:11042:97597.effective_date ==
  date(1996, 1, 1)` (assert against whatever real format Step 1 fixes the fixture to).
- The "* prior to 1996" marker loads as a boolean — the `11042/97597` row →
  `in_existence_prior_1996 is True`; the `93000/93005` row → `False`.
- Every loaded edit's CCMI is one of {0, 1, 9} — see §7a.1: this must be a **named-row +
  negative-case** test, not the tautology that currently stands (the SPEC §6 "CCMI ∈ {0,1,9}"
  DoD).
- A known authoritative pair is present after load — `"PTP:11042:97597"` is in the set of
  loaded `rule_id`s (the SPEC §6 "known pair present" DoD).
- No data row is silently dropped — `load_ptp_table(fixture)` returns exactly the known data-row
  count with the exact expected `rule_id` set (the SPEC §6 "counts match" DoD; see §7a.2 — the
  oracle must be a literal, not a re-parse of the source).
- A malformed code raises — a source row whose column code is not a 5-char HCPCS/CPT code
  (e.g. `"1104"`) makes the loader raise `ValidationError`; the bad row is **not** skipped.

Policy manual (`load_policy_sections`):
- A known chapter loads keyed by its label with non-empty text — the returned dict contains
  the expected chapter key and its value is a non-empty string (so a Phase 2
  `DirectInjectionRetriever` can consume it directly).

## 5. Definition of done (restated from SPEC §6, Phase 1 row)

`make test` green, with behavioral tests proving: **a known pair is present, the loaded count
matches the source row count, and every CCMI is in {0,1,9}**. Plus AGENTS.md §6 gates:
`ruff check` clean, and no LLM/embedding/Chroma import in the ingestion module (the seam).
Both review agents sign off (code traces to goal; tests are behavioral, not smoke).

## 6. Open questions — resolved by Step 1 (real-data reconciliation done)

Step 1 ran against the real Q3 2026 files now under `data/` (gitignored). Outcomes:

1. **Network / licensing vs. the no-egress guardrail — STANDS (by design).** The CMS download
   remains a one-time, human-performed, out-of-band step into `data/`; no Phase 1 *code* and
   nothing on the test path makes a network call (`make fetch-data` only prints URLs). This is
   the one assumption that is permanent, not "pending Step 1."
2. **Real headers — RESOLVED.** The real Practitioner file ships a 4-row merged header block
   (physical rows 2–5 of `data/ccipra-v322r0-f1/ccipra-v322r0-f1.TXT`). Merging those four rows
   per column reconstructs exactly the seven `_PTP_HEADER_TO_FIELD` keys the loader expects
   (e.g. `"Deletion Date *=no data"`, `"Modifier 0=not allowed 1=allowed 9=not applicable"`).
   `fixtures/sample_ptp.csv` mirrors this layout (CPT-copyright row, "Column1/Column2 Edits"
   row, 4-row header block, then data rows). Date format on disk is **YYYYMMDD**, so the §4
   `date(1996,1,1)` assertion stands.
3. **Segmented input — RESOLVED.** CMS ships the Practitioner table as a **single combined**
   `.TXT` (675,043 physical rows), not segments. The loader's directory mode is retained (it is
   correct and cheap) but is not exercised by the real file; §7a.3 adds a test that pins it.
4. **Deletion-date "active" sentinel — RESOLVED.** Active edits carry `*` (= "no data") **or**
   blank in the deletion-date column; both map to `None`. Confirmed by count: 443,245 rows load
   as active (`deletion_date is None`). The loader's `_deletion_date({"", "*"} → None)` matches.
5. **Chapter-key convention — RESOLVED.** The manual ships as **one combined PDF**
   (`data/2026_ncci_medicare_policy_manual_all-chapters.pdf`, 13 chapters), not per-chapter
   files. The loader splits on `^CHAPTER <ROMAN>$` and keys `"Chapter 1".."Chapter 13"`, which
   is what Phase 2's `PolicyChunk` / `DirectInjectionRetriever` consume.

## 7. Phase 1 — review outcome & required changes

Two opus reviewers ran against the on-disk implementation (not the plan).

**Verdict.** `policyforge/ingestion.py` — **GREEN** (code reviewer): every line traces to the
Phase 1 goal, the seam is intact (no model/embedding/Chroma/randomness), construction goes
through `PTPRule` so validators are the gate, no impossible-case handling. One MINOR: the plan
said `ingest.py`; the module is `ingestion.py` (fixed above).
`tests/test_ingestion.py` — **CHANGES REQUIRED → RESOLVED** (test reviewer): three tests were
smoke/circular and one DoD claim rested on a tautology; Codex fixed all three per §7a, editing
only the test file, and `make test` is green (**22 passed**), re-reviewed behavioral.
**Phase 1 is signed off (2026-06-29).** `TESTING.md` is now drafted.

### 7a. Test fixes (Codex's domain — AGENTS.md §4, tests-first behavioral)

**Status: DONE (2026-06-29)** — Codex implemented all three fixes (edited only
`tests/test_ingestion.py`); `make test` green (22 passed), re-reviewed behavioral and signed
off. The work order below is preserved for the audit trail.

**Scope — hard boundaries.** Edit **only `tests/test_ingestion.py`**. Do **not** modify
`policyforge/ingestion.py` (reviewed GREEN) or `policyforge/schemas.py` (user-owned). Do **not**
mutate `fixtures/sample_ptp.csv`; where a fix needs malformed/extra rows, build a throwaway
fixture under `tmp_path` (the existing `test_a_malformed_column_code_raises` shows the pattern).
No production code changes.

1. **`test_every_loaded_edit_has_a_valid_ccmi` is tautological — replace it.** The loader builds
   `ModifierIndicator(int(...))`, which already raises on any non-{0,1,9} value, so
   `{r.modifier_indicator} <= set(ModifierIndicator)` can never fail. It proves nothing about
   the source mapping. Replace with **named-row** assertions — `PTP:11042:97597` →
   `ModifierIndicator.ALLOWED`, `PTP:93000:93005` → `ModifierIndicator.NOT_ALLOWED`,
   `PTP:36415:36416` → `ModifierIndicator.NOT_APPLICABLE` — **and** a negative case: a source row
   whose CCMI is `7` (outside {0,1,9}) makes `load_ptp_table` raise (the real "CCMI ∈ {0,1,9}"
   gate; build the bad row in a `tmp_path` fixture).
   → **verify:** the positives fail if the CCMI column is mis-mapped (e.g. swapped with another
   column); the negative fails if an out-of-domain CCMI is silently accepted.
2. **`test_no_data_row_is_silently_dropped` uses a circular oracle — pin literals.** The helper
   `_ptp_data_row_count` re-implements the loader's own row filter (`len(row)==7 and
   row[3].isdigit()`), so a header-offset bug in the loader is mirrored by the oracle and the
   test still passes. Replace with the **literal** expected count (`== 6`) and assert the exact
   loaded `rule_id` set equals the known six pairs, then delete the `_ptp_data_row_count` helper:
   `{"PTP:11042:97597", "PTP:93000:93005", "PTP:27447:27486", "PTP:80053:80048",
   "PTP:36415:36416", "PTP:99213:99214"}`.
   → **verify:** the test fails if the loader drops a row or mis-offsets the header block,
   without consulting any parser that shares the loader's assumptions.
3. **Directory / multi-segment mode is untested — add a test.** `load_ptp_table` accepts a
   directory and concatenates files, but no test exercises it. Add a test that writes two
   one-data-row table files (each with the full header block + one data row) into a `tmp_path`
   dir and asserts both pairs load (both `rule_id`s present, count `== 2`).
   → **verify:** `load_ptp_table(tmp_dir)` returns both rules; removing the directory branch
   from the loader fails the test.

**Dispatch definition of done:** `make test` and `make lint` both green; every new/changed test
is behavioral (fails under a plausible wrong loader, not just on a crash) — confirm by briefly
mutating the loader locally then reverting for fixes 1–2; no edits outside
`tests/test_ingestion.py`; hand back the `make test` output. On green, the planning agent drafts
TESTING.md (currently gated on this).

### 7b. Schema carry-overs (user's domain — CLAUDE.md §3, a schema change is its own task)

These are pre-existing schema behaviors Phase 1 surfaced. They are **not** Phase 1 edits; they
are flagged for a user-owned, separately-reviewed schema task:

1. **`PTPRule.is_active_on` deletion boundary uses `>=`.** A claim dated exactly on the deletion
   date is treated as inactive. CMS deletion-date convention must be confirmed: if "deletion
   date = first inactive day," `>=` is correct; if "last active day," it should be `>`. Pin with
   a boundary test either way (`is_active_on(deletion_date)` and `± 1 day`).
2. **`PTPRule.to_json_logic` emits unconditional DENY for CCMI 9.** CCMI `9`
   (NOT_APPLICABLE / deleted edit) should not compile to a DENY rule at all — a deleted edit
   does not adjudicate. Confirm whether `to_json_logic` should raise/skip for `9` rather than
   deny. (Phase 4 must also treat CCMI 9 as "no action" — see below.)

---

**Scope guard (Phase 1):** Practitioner PTP only — no MUE, no principle-level manual guidance
that names no codes (SPEC §2). Phase 1 imports no model and writes no schema field. If the data
would be easier with a field it doesn't have, the answer is no (CLAUDE.md §3).

---

# Phase 4 — Deterministic Engine (planned)

> Implements the Phase 4 row of SPEC §6: `adjudicate(...) -> ClaimDisposition`, all CCMI/date
> scenarios pass, **zero LLM imports**. This is the engine half of the seam — the part that
> *decides*. It reads `PTPRule`s and a `Claim`; it never reads prose, never calls a model. Per
> SPEC's dependency note, Phase 4 sits off Phase 0 (it needs only the schemas, not Phase 1's
> loaded data) and is scheduled early because it unlocks Track B — the un-fakeable proof that
> the rules adjudicate correctly.

## 1. Objective

Given a `Claim` and the active PTP ruleset, decide each line deterministically — `PAY`, `DENY`
(reason `CO-97`), or `FLAG` — citing the exact `rule_id` and `ruleset_version` for every
non-PAY line, with no model, embedding, or randomness on the path.

## 2. Interfaces

New module: `policyforge/engine.py`. Returns contract types from `policyforge.schemas` only —
no new schema fields, no parallel shapes.

```python
def adjudicate(claim: Claim, rules: list[PTPRule], ruleset_version: str) -> ClaimDisposition: ...
```

- Iterates the claim's lines, finds PTP edits whose `(column_1, column_2)` both appear on the
  claim, applies the SPEC §5 sequence, and returns a `ClaimDisposition` with one
  `LineDisposition` per claim line.
- **DECIDED — `ruleset_version` is a parameter, not a schema type.** SPEC §6 writes the
  signature as `adjudicate(claim, ruleset)`, but no `Ruleset` schema type exists and
  `ClaimDisposition.ruleset_version` is required. Resolved: `ruleset` is expanded into
  `(rules: list[PTPRule], ruleset_version: str)` — smallest surface, no schema change; the
  caller (Phase 5 eval / demo) already knows the version string it loaded, and Phase 4 stays in
  its lane. A `Ruleset` schema type (`version` + `rules`) was considered and **deferred**: if it
  is ever wanted it is a separate, user-owned, reviewed schema task (CLAUDE.md §3), never a
  Phase 4 side effect.

## 3. Steps (each with its verify line)

1. **Index the ruleset for lookup.** Build a `{(column_1, column_2): PTPRule}` map once per
   call. Deterministic; no I/O.
   → **verify:** `pytest tests/test_engine.py -k lookup` green.
2. **Implement the SPEC §5 line sequence.** For each ordered pair of lines `(a, b)` on the claim
   **with `a.date_of_service == b.date_of_service`** (PTP bundles only same-DOS services; bene +
   provider are already claim-level constants), if a PTP edit exists for the directed pair
   `(a.code, b.code)` **and** the rule `is_active_on(that shared DOS)`: CCMI `0` → `DENY` the
   column-2 line `b` (reason `CO-97`); CCMI `1` → `FLAG` `b` if **line `b` (the column-2 line)**
   carries a modifier in `NCCI_PTP_ASSOCIATED_MODIFIERS`, else `DENY`; CCMI `9` → **no action**.
   When a line is the column-2 of more than one active edit, the **most severe** disposition wins
   (`DENY` > `FLAG` > `PAY`), citing the rule that produced it, ties broken by `rule_id` sort.
   Lines with no triggering active edit → `PAY`. Output **one `LineDisposition` per claim line**
   (PAY lines carry `reason_code is None` and `cited_rule_id is None`); every `DENY`/`FLAG` cites
   `rule_id` + `ruleset_version` and a human-meaningful `explanation` (naming the modifier for
   FLAG, the bundling for DENY).
   → **verify:** `pytest tests/test_engine.py` green (scenarios in §4).
3. **Seam check.** No model / embedding / Chroma / randomness import in `policyforge/engine.py`.
   → **verify:** `grep -nE "anthropic|chromadb|embedding|random" policyforge/engine.py` is
   empty; `make lint` clean.
4. **Phase gate.** → **verify:** `make test` green.

## 4. Behavioral test expectations (Codex turns each sentence into a named test)

Built on **hand-constructed `PTPRule` + `Claim` fixtures** (no Phase 1 dependency — this is why
Phase 4 can run off Phase 0). Each name is a policy scenario; each survives a from-scratch
rewrite of the engine. **No smoke.**

- A CCMI-0 pair on one claim denies the column-2 line with CO-97 — two lines `93000` + `93005`,
  rule `PTP:93000:93005` CCMI 0 → the `93005` line is `DENY`, `reason_code == "CO-97"`,
  `cited_rule_id == "PTP:93000:93005"`; the `93000` line is `PAY`.
- A CCMI-1 pair with a bypass modifier flags, not denies — `11042` + `97597` with modifier `59`
  on the column-2 line → that line is `FLAG` (cites the rule), not `DENY`.
- A CCMI-1 pair without a bypass modifier denies — same pair, no modifier → `DENY` CO-97.
- A CCMI-9 edit takes no action — a `9` edit for two billed codes → both lines `PAY`; no
  disposition cites that rule.
- An edit not active on the date of service does not fire — a pair whose rule deletion date is
  before the line's DOS → `PAY` (guards the `is_active_on` wiring).
- A pair the ruleset never mentions pays — two unrelated codes → both `PAY`.
- Column order matters — an edit `(A, B)` does not fire when the claim bills `B` as column-1 and
  `A` as column-2 with no `(B, A)` edit (PTP edits are directional).
- Same date of service is required — the same CCMI-0 pair billed on two **different** dates of
  service → both `PAY` (PTP bundles only same-DOS services; pins the §3 step-2 DOS constraint).
- The bypass modifier must be on the column-2 line — a CCMI-1 pair with the modifier on the
  **column-1** line instead of column-2 → `DENY`, not `FLAG`.
- Most-severe-wins across edits — a line that is the column-2 of a CCMI-0 edit (→ DENY) and a
  CCMI-1+modifier edit (→ FLAG) is `DENY`, citing the CCMI-0 rule.
- Every line gets a disposition — a fully clean claim yields one `PAY` `LineDisposition` per line,
  each with `reason_code is None` and `cited_rule_id is None`.
- Every non-PAY line cites a rule and the ruleset version — for any `DENY`/`FLAG`,
  `cited_rule_id` is set and `disposition.ruleset_version` equals the version passed in.

## 5. Definition of done (SPEC §6, Phase 4 row)

`make test` green with the §4 scenarios passing (all CCMI branches + date-window), **zero
LLM / embedding / Chroma imports** in `engine.py` (the seam, grep-verified), `ruff` clean, and
both reviewers signing off (every line traces to the SPEC §5 sequence; tests behavioral, not
smoke).

## 6. Open questions (explicit assumptions)

1. **`ruleset_version` source — DECIDED (§2):** a `ruleset_version: str` parameter; no `Ruleset`
   schema type. Closed.
2. **Bypass-modifier set — DECIDED (2026-06-30): pinned module constant** (user-supplied list).
   Encode in `engine.py` as deterministic policy data (not a model input) and pin it with a test:
   ```python
   NCCI_PTP_ASSOCIATED_MODIFIERS = {
       # Anatomic modifiers
       "E1", "E2", "E3", "E4",
       "FA", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9",
       "TA", "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9",
       "LT", "RT", "LC", "LD", "RC", "LM", "RI",
       # Global surgery modifiers
       "24", "25", "57", "58", "78", "79",
       # Other NCCI PTP-associated modifiers
       "27", "59", "91",
       "XE", "XP", "XS", "XU",
   }
   ```
3. **Multiple edits touching one line — DECIDED (2026-06-30):** most-severe-wins
   (`DENY` > `FLAG` > `PAY`), cite the producing rule, ties broken by `rule_id` sort. Promoted from
   assumption to a named behavioral test (§4).
4. **Units / quantity.** PTP is a pair edit, not a quantity edit (that is MUE, out of scope per
   SPEC §2). **Assumption:** `units` does not affect PTP adjudication; a denied line is denied
   regardless of units.
5. **Same-DOS pairing — DECIDED (2026-06-30):** edits fire only between lines with equal
   `date_of_service` (SPEC §5). Encoded in step 2 + a §4 test.
6. **Bypass-modifier location — DECIDED (2026-06-30):** the modifier must be on the column-2
   (denied) line to FLAG; a modifier on the column-1 line does not bypass. Encoded in step 2 + a
   §4 test.

---

**Scope guard (Phase 4):** PTP pair logic only — no MUE/quantity, no prose, no model. The engine
reads schemas and decides; it writes no schema field and crosses no seam (CLAUDE.md §2–3).

---

## Phase 4 — review outcome & required changes

Two opus reviewers ran against the on-disk engine (`make test` = 57 passed, `ruff` clean, seam grep
empty going in).

**Verdict: code GREEN; tests CHANGES REQUIRED (light) → NOT yet signed off.**

- **Code (`engine.py`) — GREEN.** All 15 decided semantics verified against SPEC §5 (same-DOS
  enforced before lookup, directional column-2 denial, date logic delegated to `is_active_on`, CCMI
  0/1/9 branches, column-2-only modifier, most-severe-wins with an order-independent `rule_id`
  tie-break, one disposition per line, `CO-97`, modifier constant pinned, no units/MUE).
  Deterministic; seam intact. No MAJOR.
- **Tests (`test_engine.py`) — CHANGES REQUIRED.** All 13 tests are behavioral (no
  smoke/tautology/circular; the pinned-set test compares to a literal). But two pieces of
  *implemented, decision-affecting* behavior have zero coverage and must be tested before sign-off.

### Test additions (Codex — `tests/test_engine.py` only)

1. **Determinism + line-order.** Assert the same claim+ruleset yields identical
   `(line_id, status, reason_code, cited_rule_id)` tuples across two `adjudicate` calls, and that
   dispositions return in claim-line order.
   → **verify:** fails if output becomes order-dependent or loses line order.
2. **`rule_id` tie-break (the real gap).** When one line is the column-2 of **two same-severity**
   active edits, the smaller `rule_id` is cited, order-independently — the `_beats` tie-break that no
   current test exercises. E.g. bill `11042`+`36415`+`97597`; edits `(36415,97597)` and
   `(11042,97597)` both CCMI-0 → line `97597` cites `PTP:11042:97597` regardless of rule list order.
   → **verify:** fails if the tie-break is dropped or made iteration-dependent.
3. **Minor strengthenings (optional):** assert `reason_code` in the column-order test; assert
   `cited_rule_id` in the column-2-modifier test; make the "every non-PAY line cites" invariant use
   two non-PAY lines (one DENY + one FLAG) so the `all(...)` bites on n>1.

### Code notes (not blocking; recorded)

- NIT: `engine.py:71` `line` param missing a type annotation (cosmetic, ruff-clean).

### Re-review (2026-06-30) — test additions delivered; one defect + dup-pair decision

The 2 test additions landed (`make test` = 71 passed). A second two-agent pass (Opus ×2) found:

1. **REQUIRED — the `rule_id` tie-break test does NOT bite.**
   `test_same_severity_edits_cite_the_smaller_rule_id_independent_of_rule_order` proves nothing:
   the only thing it reorders (the rules **list**) is inert against a dict keyed by pair, and its
   claim-line order (`L1=11042, L2=36415`) coincides with the smaller `rule_id`, so a no-tie-break
   engine (`_beats` returns `False` on a tie → first-seen-wins) passes unchanged. **Reproduced by
   mutation.** Fix: reorder claim lines to **`L1=36415, L2=11042, L3=97597`** and assert `L3` cites
   `PTP:11042:97597` — verified this fails the mutation and passes the correct engine.
   → **verify:** mutate `_beats` to drop the `rule_id` comparison; the test must fail.

2. **DECIDED — duplicate `(col1,col2)` rows: FIX NOW (was "defer"), see DECISIONS ADR-011.**
   Measured: the real table is **21.08% (142,311/675,037) rows in duplicated pairs** with
   non-overlapping windows; the 100-row gold set has **0** and `run_eval` feeds gold rules (not the
   table), so Track B (105/105) is unaffected. Harden `adjudicate` (group per pair → pick
   `is_active_on(DOS)` → tie-break by `rule_id`) + add a same-pair/two-windows behavioral test.

### Sign-off (2026-06-30) — GREEN-with-nits → SIGNED OFF

Codex landed the fix bundle; `make test` = **76 passed**, `ruff` clean, seam grep empty. A third
two-agent pass (Opus ×2, **scoped to the changed regions only**) confirmed:
- **Engine dup-pair fix** correct and seam-clean; Track B re-verified **105/105 with an identical
  confusion matrix** with and without the change (`pay/pay=130, deny/deny=40, flag/flag=20`).
- **Tie-break test now bites** — mutating `_beats` to drop the `rule_id` comparison fails it; the new
  dup-pair test fails under last-write-wins. Both verified by mutation.
- One **LOW nit** (vacuous within-bucket tie-break key, `engine.py:65`) — zero impact on NCCI-shaped
  data; **accepted as a tracked follow-up** (DECISIONS "Open / pending"), not a blocker.

**Phase 4 is signed off (2026-06-30). Track B is unlocked.** TESTING.md §6 covers it.

---

# Phase 2 — Retriever arms (planned)

> Implements the Phase 2 row of SPEC §6: `DirectInjectionRetriever` + `ChromaRetriever`, each
> returning the chapter that holds a known example. This is the **LLM / conversion side** of the
> seam (SPEC §3) — its only consumers are Phase 3 extraction and the Phase 5 ablation. Embeddings
> and Chroma are *allowed and expected here*; the seam (CLAUDE.md §2) bars them only from the
> Phase 4 adjudication path, never from retrieval. The retriever still makes **no model call**
> (no `anthropic` import) — turning text into chunks is not extraction.

**Status: DISPATCHED to Codex (2026-06-29), bundled with Phase 3.** Scope: implement the bodies in
`policyforge/retriever.py` + the `build_chroma_index` builder, and add `tests/test_retriever.py`.
Do **not** modify `policyforge/schemas.py` (user-owned) or other modules. Retriever and Extraction
are independent modules (no import between them) and may be built in parallel.

## 1. Objective

Implement the two retriever bodies scaffolded in Phase 0 (`policyforge/retriever.py`) so that,
given the chapter-keyed corpus from `load_policy_sections`, a query naming a code pair returns
the policy chapter(s) most relevant to it — via direct chapter injection (CONTROL) and via vector
search (TREATMENT) — letting Phase 5 measure whether retrieval actually helps extraction.

## 2. Interfaces

Implement the existing contract in `policyforge/retriever.py`. Do **not** change the `Retriever`
ABC or the `retrieve()` signature, and add no schema fields (`PolicyChunk` is fixed):

```python
class DirectInjectionRetriever(Retriever):   # name = "direct"
    def __init__(self, corpus: dict[str, str]) -> None: ...
    def retrieve(self, query: str, k: int = 5) -> list[PolicyChunk]: ...

class ChromaRetriever(Retriever):             # name = "chroma"
    def retrieve(self, query: str, k: int = 5) -> list[PolicyChunk]: ...
```

- **DirectInjectionRetriever** — the CONTROL. Deterministic lexical match of the query against the
  corpus chapters; return up to `k` `PolicyChunk(chapter=label, text=chapter_text, score=None)`
  for the chapters whose text contains the query terms (the code pair), best-match first. No
  embeddings, no model. `score is None` is the control's signature.
- **ChromaRetriever** — the TREATMENT. Chunk the corpus, embed each chunk via a **local Ollama
  embedding server** (base URL + model name from env — see §6.1), index in a Chroma collection,
  and `retrieve()` returns the top-`k` chunks by vector similarity as
  `PolicyChunk(chapter=…, text=chunk, score=distance)`. Read-only and deterministic for a fixed
  corpus + query + pinned model. The embedding call is loopback to localhost (not external
  egress), and the embedding function is **injectable** so `make test` runs offline (see §4).

**Two interface gaps in the Phase 0 stub (Phase 2 closes them; retriever-module-local, not a
schema change):** the stub `ChromaRetriever.__init__(collection_name, embedding_model)` has **no
way to load the corpus** and **no `chunk_size`** (its class docstring says chunk size should be a
ctor arg). **Recommended:** a module-level builder
`build_chroma_index(corpus, *, collection_name, embedding_fn, chunk_size) -> ChromaRetriever`
plus a `chunk_size` arg, leaving `retrieve()` untouched. `embedding_fn` defaults to an Ollama
embedding function built from env (`POLICYFORGE_OLLAMA_BASE_URL` + `POLICYFORGE_EMBEDDING_MODEL`)
and is injected with a deterministic fake in tests. Confirm the shape in review.

## 3. Steps (each with its verify line)

1. **`DirectInjectionRetriever.retrieve`.** Lexical match query tokens → chapters; return up to
   `k` chunks with `score=None`.
   → **verify:** the docstring BDD test passes — corpus where `"Chapter 1"` documents
   `11042/97597`; `retrieve("11042 97597")` returns a `PolicyChunk` for `"Chapter 1"`.
2. **Chroma indexing + retrieve.** Chunk corpus, embed via the injected embedding function
   (Ollama in prod, deterministic fake in tests), index, query top-`k`.
   → **verify:** the docstring BDD test passes with the fake embedder — a query whose answer
   lives in chapter N puts chapter N in the top-`k`.
3. **Contract invariants for both arms.** Every chunk has non-empty `text` and a real `chapter`;
   at most `k` returned; identical results on a repeated identical query (determinism); retrieval
   mutates neither corpus nor collection.
   → **verify:** the §4 invariant tests pass for both arms.
4. **Seam + hygiene.** No `anthropic` import in `policyforge/retriever.py` (retrieval ≠ model
   call); `chromadb` + a localhost Ollama embedding call are allowed here (LLM side, loopback —
   no external egress, no cloud).
   → **verify:** `grep -n "anthropic" policyforge/retriever.py` is empty; `make lint` clean.
5. **Phase gate.** → **verify:** `make test` green.

## 4. Behavioral test expectations (Codex turns each sentence into a named test)

Built on a tiny hand-built corpus dict (no PDF needed). **`make test` stays hermetic**: the
Chroma arm is tested with an **injected deterministic fake embedding function** (a small
pure-Python `text -> vector` map), never a live Ollama — so the suite keeps its no-network,
deterministic guarantee. A single **optional integration test** may hit real Ollama, `skip`ped
when `POLICYFORGE_OLLAMA_BASE_URL` is unreachable. **No smoke** — "returned a list" never counts.

- Direct injection returns the chapter that documents a known pair — the BDD test above.
- Chroma (with the fake embedder) returns the chapter whose text answers the query within top-`k`.
- Both arms cap at `k` — `len(retrieve(q, k=1)) <= 1`.
- The control's chunks carry `score is None`; the treatment's chunks carry a real numeric `score`.
- Every returned chunk has non-empty `text` and a non-empty `chapter` (the `Retriever` contract).
- A query that matches nothing returns an empty list **(control arm)** — the lexical control never
  fabricates a chunk. The **treatment arm returns the top-k nearest by similarity** (a kNN index
  always has neighbors); "no fabrication" there means it only ever returns real corpus chunks, never
  invented text. (If symmetric empty-on-no-match is wanted for the treatment, gate on a distance
  threshold — a semantic signal — not a lexical one. See the review outcome.)
- Retrieval is deterministic — the same query twice returns the same chapters in the same order.
- (optional) Integration: with real Ollama reachable, the treatment arm indexes the fixture
  corpus and returns the expected chapter; `skip`ped otherwise.

## 5. Definition of done (SPEC §6, Phase 2 row)

`make test` green with both arms **returning the chapter holding a known example** (the §6 DoD);
`PolicyChunk` invariants hold; `ruff` clean; no `anthropic` import in the retriever; both
reviewers sign off (behavioral tests; control/treatment cleanly separated for the Phase 5 ablation).

## 6. Open questions (explicit assumptions — #1 needs a decision before build)

1. **Embedding model — DECIDED (2026-06-29): local Ollama, semantic embeddings (option a).** The
   treatment arm embeds via a **local Ollama server** (the user already runs one), configured from
   env: `POLICYFORGE_OLLAMA_BASE_URL` (default `http://localhost:11434`) +
   `POLICYFORGE_EMBEDDING_MODEL` (the Ollama embedding model, pinned for reproducible retrieval).
   This is **loopback, not external egress** — consistent with the no-cloud / no-egress guardrail.
   The model is pinned for determinism; `make test` never calls it (injected fake embedder, §4).
   The offline TF-IDF fallback (option b) is dropped.
2. **Indexing entrypoint + `chunk_size`** — see §2; assume the module-level `build_chroma_index`
   builder + a `chunk_size` ctor arg unless the user prefers extending `__init__`.
3. **DirectInjection match strategy.** **Assumption:** substring/token match of the query's codes
   against chapter text, ranked by hit count — the simplest deterministic control. A chapter that
   names many pairs may match several queries; fine for the control.
4. **Chunking strategy for Chroma.** **Assumption:** fixed-size character/token windows with small
   overlap; each chunk carries its source `chapter` as provenance. Confirm size in review.

---

**Scope guard (Phase 2):** retrieval only — no extraction (Phase 3), no model call, no schema
fields, no engine/adjudication code. Both arms behind the existing `Retriever` ABC; the
control/treatment split exists so Phase 5 can report the delta, not to bake Chroma in blindly.

---

# Phase 3 — LLM Extraction (planned)

> Implements the Phase 3 row of SPEC §6: `extract_rules(text) -> list[RuleCandidate]`. The
> **conversion seam itself** — the one place an LLM runs in this project's value path. It turns
> manual prose into *candidate* rules; candidates are **not** rules — they carry provenance +
> self-reported confidence for Track A scoring (Phase 5) and the human gate (Phase 6), and only
> become a `PTPRule` after a human approves (SPEC §3, `RuleCandidate` docstring). Extraction never
> adjudicates, never writes a store, never emits a `PTPRule`.

**Status: DISPATCHED to Codex (2026-06-29), bundled with Phase 2.** Scope: create
`policyforge/extraction.py` + `tests/test_extraction.py`. Do **not** modify
`policyforge/schemas.py` (user-owned). Hermetic tests (injected fake client); **no live API call in
`make test`** — the optional real-API test `skip`s without `ANTHROPIC_API_KEY`.

## 1. Objective

Given a span of NCCI Policy Manual text, use Claude to extract the **subset where the prose names
concrete code pairs** into validated `RuleCandidate`s — faithful to what the text actually says,
every candidate citing the sentence it came from — so Phase 5 can score conversion fidelity
(Track A) honestly and Phase 6 can gate the diff.

## 2. Interfaces

New module: `policyforge/extraction.py`. Returns the existing `RuleCandidate` contract from
`policyforge.schemas` only — no new schema fields.

```python
def extract_rules(text: str, source_chapter: str, *, client=<injected>, model=<env>) -> list[RuleCandidate]: ...
```

- Sends `text` to Claude with a structured-output (tool-use) request shaped to `RuleCandidate`'s
  fields; parses each returned object **through `RuleCandidate(...)`** so Pydantic is the gate —
  malformed output raises `ValidationError`, it is not coerced or dropped silently.
- The model is **pinned** via `POLICYFORGE_EXTRACTION_MODEL` (env; e.g. `claude-sonnet-4-6`) for
  reproducible eval numbers (SPEC §9).
- **`client` is injectable** so `make test` stays hermetic (a fake returning canned tool output);
  in prod it defaults to an `anthropic.Anthropic()` reading `ANTHROPIC_API_KEY`. The Anthropic API
  is the one permitted network call (guardrail).

**Interface gap vs the SPEC signature.** SPEC §6 writes `extract_rules(text)`, but
`RuleCandidate.source_chapter` is **required** and the text alone doesn't reliably name its own
chapter. **Recommended:** add a `source_chapter: str` parameter (the caller — retriever / eval —
already has the label from `PolicyChunk.chapter` / `load_policy_sections`), rather than have the
model guess provenance. `source_quote` and `extraction_confidence` come from the model. Confirm in
review.

## 3. Steps (each with its verify line)

1. **Prompt + structured output.** A faithful extraction prompt: emit a candidate **only** where
   the text explicitly names a Column-1/Column-2 pair and its modifier indicator; fill
   `source_quote` with the verbatim sentence and `extraction_confidence` honestly; do **not**
   complete pairs from prior knowledge. Request structured (tool-use) output shaped to
   `RuleCandidate`.
   → **verify:** with a canned valid tool response, `extract_rules` returns `RuleCandidate`s whose
   pair + CCMI + provenance match the fixture text (injected fake client).
2. **Pydantic is the gate.** Parse every model object through `RuleCandidate(...)`.
   → **verify:** a canned **malformed** response (bad code / missing field / CCMI=7 /
   confidence>1) makes `extract_rules` raise `ValidationError`; the bad candidate is not skipped.
3. **Grounding signal — measure, don't hide.** Provide a pure helper
   `is_quote_grounded(candidate, text) -> bool` (`source_quote` is a substring of `text`).
   `extract_rules` **retains** ungrounded candidates faithful to the model output (its
   `extraction_confidence` is **not** overwritten); grounding is a derived, reported signal — a
   hallucination-rate metric for Phase 5 and a low-trust flag for the Phase 6 gate, **not** a
   filter (DECIDED §6.3).
   → **verify:** `is_quote_grounded` is `False` when the quote is absent from `text`, `True` when
   present; `extract_rules` still returns the ungrounded candidate (it is not dropped).
4. **Seam check.** `extract_rules` emits `RuleCandidate` only — never a `PTPRule`, never a
   disposition; no store write, no adjudication, no engine import.
   → **verify:** `grep -nE "PTPRule|adjudicate|ClaimDisposition|sqlite" policyforge/extraction.py`
   is empty; `make lint` clean.
5. **Phase gate.** → **verify:** `make test` green.

## 4. Behavioral test expectations (Codex turns each sentence into a named test)

Hermetic: a **fake client** returns canned tool output; **no live API call** in `make test`. One
optional integration test hits the real API, `skip`ped without `ANTHROPIC_API_KEY`. **No smoke.**

- A pair named in the prose is extracted with its CCMI and provenance — fixture text naming
  `11042/97597` (CCMI 1) → a `RuleCandidate` with that pair, `ALLOWED`, `source_chapter` set, and a
  `source_quote` drawn from the text.
- Malformed model output is rejected by Pydantic — a canned object with a 4-char code (or CCMI 7,
  or `extraction_confidence` 1.5) raises `ValidationError`, not a silently-dropped row (the SPEC §6
  "malformed rejected by Pydantic" DoD).
- An ungrounded quote is retained and measurable — a candidate whose `source_quote` is not a
  substring of `text` is still returned by `extract_rules`, and `is_quote_grounded` reports it as
  ungrounded (it becomes evidence of hallucination rate, not a silent drop).
- Prose that names no code pair yields no candidates — principle-only text → `[]` (the model must
  not fabricate pairs; SPEC §2 scope + §9 honest eval).
- A candidate is scorable against the table — `RuleCandidate.matches(authoritative_PTPRule)` is
  `True` for a correctly-extracted pair and `False` on a CCMI mismatch (ties Phase 3 output to the
  Track A oracle).
- (optional) Integration: real API extracts ≥1 valid candidate from a real chapter snippet;
  `skip`ped without a key.

## 5. Definition of done (SPEC §6, Phase 3 row)

`make test` green: **valid model output parses to `RuleCandidate`s; malformed output is rejected by
Pydantic.** Provenance (`source_chapter` / `source_quote` / `extraction_confidence`) populated; the
the grounding signal is computed and reported (ungrounded candidates retained, not dropped); `extract_rules` emits candidates only (seam intact, grep-verified); model
pinned via env; `ruff` clean; both reviewers sign off — and the extraction prompt is **not** tuned
toward an inflated fidelity number (CLAUDE.md §6, SPEC §9).

## 6. Open questions (explicit assumptions)

1. **`source_chapter` parameter** — see §2; assume `extract_rules(text, source_chapter, ...)`
   (caller supplies the label) unless the user prefers the model infer it from the text.
2. **Client injection + structured-output mechanism** — assume an injectable `client` and tool-use
   structured output; tests pass a fake. Confirm vs. a thin transport seam.
3. **Ungrounded quote handling — DECIDED (2026-06-29): RECORD, don't drop.** `extract_rules` keeps
   ungrounded candidates as faithful model output (the model's `extraction_confidence` is **not**
   overwritten); grounding is computed by `is_quote_grounded` and reported as a hallucination-rate
   signal (Phase 5) + a low-trust flag at the human gate (Phase 6). Rationale: surfacing
   hallucinations as a measured number is *more* honest than hiding them by dropping (SPEC §9) —
   they become evidence of system performance. **Optional follow-up (user-owned schema task):** add
   `quote_grounded: bool` to `RuleCandidate` to persist the flag on the object; until then grounding
   is derived at eval/gate time from the candidate + its source text (no schema change needed to
   dispatch).
4. **Batch vs. per-chunk.** **Assumption:** `extract_rules` handles one text span; the eval / orch
   layer (Phase 5) loops chunks/chapters and dedups candidates. Keeps Phase 3 single-purpose.
5. **Confidence semantics.** **Assumption:** `extraction_confidence` is the model's self-report,
   recorded for the gate, **not** used to silently filter (filtering would game Track A). Confirm.

---

**Scope guard (Phase 3):** conversion only — extract `RuleCandidate`s from prose that names code
pairs; no engine, no store, no `PTPRule`, no schema fields. The LLM converts and stops at the seam;
the human gate (Phase 6) and the deterministic engine (Phase 4) are downstream and separate. The
prompt is honest by construction (quote-grounded, no knowledge-completion) — a suspiciously high
Track A score is a red flag, not a win (CLAUDE.md §6).

---

# Phase 2 & 3 — review outcome & required changes

Two opus reviewers ran against the on-disk implementation (`make test` = 41 passed, `ruff` clean
going in — so this is depth, not "does it run").

**Verdict: CHANGES REQUIRED** (both reviewers). The seam is intact on both modules (no `anthropic`
in `retriever.py`; `extraction.py` emits only `RuleCandidate`, malformed output raises with no
try/except, ungrounded candidates retained with confidence untouched, `is_quote_grounded` is a pure
substring check, honest-by-construction prompt). Headline DoDs rest on behavioral tests. Two MAJOR
defects the green hermetic suite structurally cannot catch, plus three small test fixes.

### A. Code fixes (Codex — `policyforge/`)

1. **MAJOR — `retriever.py:148-150`: `ChromaRetriever` post-filters vector hits with a lexical
   AND-match (`_lexical_score`, requires every query term verbatim), collapsing the TREATMENT arm
   into the CONTROL and making the Phase 5 ablation uninterpretable.** Remove the lexical gate;
   return top-k by vector similarity. (No-match behavior → see §C.)
   → **verify:** a query semantically near but lexically disjoint from the target chunk returns that
   chunk (a test the lexical gate would fail); the Chroma arm no longer requires verbatim code
   presence.
2. **MAJOR — `extraction.py:81-100`: the generated tool `input_schema` has a dangling
   `#/$defs/ModifierIndicator`** (verified — `$defs` lands under `items`, not the schema root), so
   the CCMI enum constraint is lost and the real API may reject the tool. The `FakeClient` ignores
   `input_schema`, so the 41 green tests miss it. Hoist `$defs` to the `input_schema` root, or inline
   the enum as `{"type": "integer", "enum": [0, 1, 9]}`.
   → **verify:** every `$ref` in `_rule_candidate_tool()` resolves (unit test, §B.6).
3. **MINOR — `retriever.py:123-127`: dead constructor state** (`_embedding_model`,
   `_collection_name`, `_chunk_size` stored but never read in `retrieve()`; `_ollama_embedding_fn`
   reads the env var itself). Drop the unused fields, or wire `_embedding_model` into
   `_ollama_embedding_fn` so the stored value is the one used.
   → **verify:** no stored ctor field is unread.
4. **NON-ISSUE — `extraction.py:15` default `claude-sonnet-4-6`** is a real current model id
   (Sonnet 4.6). No change.

### B. Test fixes (Codex — `tests/`)

5. **MUST — `test_retriever.py:96` tautological + smoke** (`all(chunk.text ...)` cannot fail —
   `PolicyChunk.text` has `min_length=1`; the only real assertion is `assert chunks`). Replace with a
   chapter-selection oracle: query `"93000 93005"` → `chapter == "Chapter 11"` for both arms.
   → **verify:** the test fails if the wrong chapter is returned.
6. **SHOULD — `test_retriever.py:82` loose k-cap** (`<= 1` also passes on an empty return). Use a
   query matching ≥2 chapters and assert `k=2 → len == 2` and `k=1 → len == 1`.
   → **verify:** fails if `k` is ignored OR the arm under-returns.
7. **SHOULD — `test_extraction.py:155` overclaims** ("model must not fabricate" is untested — the
   fake client is handed `[]`, so it only exercises `[]`-in/`[]`-out plumbing). Rename to reflect
   that, or add the optional real-API integration test
   (`@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"))`) asserting principle-only prose →
   `[]` from the live model.
   → **verify:** the test's name matches what it asserts, or the integration test exists and skips
   cleanly without a key.
8. **NEW — add the `$ref`-resolution test** from §A.2 so the production tool schema is guarded
   without a live call.

### C. Planner decision — DECIDED (2026-06-29): treatment returns pure top-k

The treatment (Chroma) arm returns its k nearest chunks by similarity; **"empty on no match" is a
CONTROL-only invariant** (PLAN §4 reflects this). §A.1 is unblocked.

**Design rationale (carry into the Phase 5 eval report / README):**
- *Why top-k.* A kNN vector index always has nearest neighbors; "top-k always" is the honest,
  knob-free representation of what vector search does. It keeps the Phase 5 ablation interpretable —
  the direct-vs-Chroma delta reflects retrieval quality, not a hidden cutoff — and it cannot silently
  suppress results, so Track A precision differences are attributable to the arms, not a threshold.
- *Alternative considered — distance threshold.* Return `[]` when the nearest distance exceeds a
  cutoff (symmetric empty-on-no-match).
  - *Betterment:* more intuitive "no relevant policy → nothing"; could drop obviously irrelevant
    chunks before extraction, trimming Phase 3 token cost.
  - *Problem:* introduces a tunable knob that must be justified and pinned; a mis-set threshold
    silently changes recall and confounds the ablation (the exact failure §A.1 fixes). Deferred — if
    adopted later it is its own reviewed change with the cutoff pinned and reported.

**Re-review (2026-06-29): GREEN — Phases 2 & 3 SIGNED OFF.** Both opus re-reviewers confirmed every
§A/§B item fixed and behavioral, the seam intact, and `make test` (44 passed) hermetic; `ruff` clean.
TESTING.md updated (§4 retriever, §5 extraction, status flipped).

**Optional, non-blocking cleanups (recorded; did not gate sign-off):**
- `retriever.py:121` — drop the now-dead `chunk_size` param on `ChromaRetriever.__init__` (chunking
  lives in `build_chroma_index`).
- `retriever.py:147` — drop the `if not document: continue` impossible-case guard (`PolicyChunk.text`
  has `min_length=1`; only non-empty chunks are indexed).
- `retriever.py:128/131` — faint lexical vestige: `retrieve` returns `[]` when the query has no
  token ≥3 chars / no 5-char code; harmless for code-pair queries but a residual lexical coupling on
  the treatment arm. Optional: gate only on empty query / `k <= 0`.
- `test_retriever.py:113-114` — delete the two decorative `all(chunk.text/chapter ...)` lines (the
  `== 2` assertion already carries the test).

---

# Phase 5 — Eval (Tracks A & B) (planned)

> Implements the Phase 5 row of SPEC §6: `run_eval() -> EvalReport`. The **evaluation seam**
> (SPEC §3 box 5). It measures two things, honestly: **Track A** = LLM extraction fidelity vs the
> authoritative table, run through *both* retriever arms and reported as a delta (the Chroma
> ablation); **Track B** = adjudication correctness of the deterministic engine on synthetic claims,
> the un-fakeable north star (SPEC §7). The model appears **only** on the Track A measurement path;
> Track B uses `adjudicate` and nothing else. Depends on Phases 1–4.

## 1. Objective

Produce an `EvalReport`: one `TrackAResult` per retriever arm (precision/recall/F1 of candidates vs
the table, via `RuleCandidate.matches`) and one `TrackBResult` (claims vs table+modifier logic,
100% on the fixture) — pure, deterministic metrics, the honest number (SPEC §9), no tuning toward a
flattering result (CLAUDE.md §6).

## 2. Interfaces

New package `policyforge/evaluation/` (`__init__.py` + `run_eval.py`). Returns `EvalReport` /
`TrackAResult` / `TrackBResult` from `policyforge.schemas` only — no new schema fields.

```python
def score_track_a(candidates: list[RuleCandidate], gold: list[PTPRule],
                  retriever_name: str) -> TrackAResult: ...                 # pure
def score_track_b(cases: list[tuple[Claim, dict[str, DispositionStatus]]],
                  rules: list[PTPRule], ruleset_version: str) -> TrackBResult: ...   # pure
def evaluate(*, rules, gold_examples, claim_cases, retrievers, extract_fn,
             ruleset_version) -> EvalReport: ...     # pure orchestration over INJECTED deps
def run_eval() -> EvalReport: ...                    # production entry; wires real deps, calls evaluate()
```

- **`matches()` is the Track A oracle** (pair + CCMI only; rationale text not scored).
- **Track B uses ONLY `adjudicate`** — no model, embedding, or randomness on the Track B path
  (the seam; SPEC §3).
- **Track B gold set (real run):** `data/ccipra-v322r0-f1/ncci_ptp_goldset_100.xlsx` — 100 labeled
  `(claim → expected_decision)` cases built from the same v322r0 table (gitignored). Scoring is
  **deterministic exact-match**: `expected_decision` maps to expected per-line `pay/deny/flag`,
  `adjudicate` runs, statuses are compared. **No LLM-as-judge** — the truth is a discrete labeled
  enum, so a judge only adds non-determinism and crosses the seam (DECISIONS ADR-010, SPEC §7,
  CLAUDE.md §2/§6). The 5 `UNCERTAIN_REVIEW_REQUIRED` (missing-context) rows are **excluded** from the
  engine's accuracy and reported separately; `ALLOW_DIFFERENT_BENEFICIARY` rows are modeled as
  **separate single-line claims** (`Claim` has one `beneficiary_id`). `make test` never reads the
  xlsx — it scores a tiny committed hand-built fixture; the gold set drives the real `make eval`.
- **Injection is the testability seam.** `evaluate()` takes the retriever arms + an `extract_fn` +
  the rules/fixtures as parameters, so `make test` exercises it with deterministic fakes and **no
  network**. `run_eval()` keeps SPEC's zero-arg signature: it loads the real table + manual
  (Phase 1), builds the `direct` + `chroma` arms (Phase 2), uses the real `extract_rules` (Phase 3),
  loads the eval fixtures, and calls `evaluate(...)`. `make eval` runs
  `python -m policyforge.evaluation.run_eval` (a `__main__` prints the report).

## 3. Steps (each with its verify line)

1. **`score_track_a`** — TP/FP/FN by `candidate.matches(gold_rule)` (a candidate is a TP iff it
   matches some gold rule; unmatched candidates are FP; gold rules with no matching candidate are
   FN), then precision/recall/F1.
   → **verify:** `pytest tests/test_eval.py -k track_a` green (scenarios in §4), incl. corrupted
   candidates lowering the score.
2. **`score_track_b`** — run `adjudicate` per case, compare each line's status to the expected map,
   tally claim-level `n_correct`/`accuracy` + line-level `confusion`.
   → **verify:** `pytest tests/test_eval.py -k track_b` green; 100% on the fixture; a wrong-expected
   case lowers accuracy and adds an off-diagonal confusion key.
3. **`evaluate()` orchestration** — score Track A for each arm (`retrieve → extract_fn → score`) and
   Track B once; assemble `EvalReport(ruleset_version, track_a=[...per arm...], track_b=...)`.
   → **verify:** with injected fakes, the report has one `TrackAResult` per arm and a `TrackBResult`.
4. **`run_eval()` + `__main__`** — wire real data/arms/extractor; load the Track B gold set
   (`ncci_ptp_goldset_100.xlsx`), map `expected_decision` → expected per-line statuses (exclude the 5
   UNCERTAIN; model `ALLOW_DIFFERENT_BENEFICIARY` as separate claims), score Track B by exact match,
   print the report + the separate out-of-scope count.
   → **verify:** `make eval` runs end-to-end on real data (manual; needs `data/` + Ollama + API + the
   xlsx loader) — **not** part of `make test`.
5. **Seam + hygiene.** The Track B path imports no model/embedding/randomness; Track A may use the
   model (it is measuring it).
   → **verify:** `grep -nE "anthropic|chromadb|embedding|random" policyforge/evaluation/run_eval.py`
   shows model use confined to the Track A / `run_eval` wiring, never in `score_track_b`; `make lint`
   clean.
6. **Phase gate.** → **verify:** `make test` green.

## 4. Behavioral test expectations (Codex turns each sentence into a named test)

Hermetic: pure scorers + `evaluate()` with **injected fakes** (a fake `extract_fn` returning canned
candidates, tiny in-memory corpus/rules). **No network in `make test`.** **No smoke.**

Track A:
- Perfect extraction scores 1.0 — candidates exactly matching the gold rules → `precision == recall
  == f1 == 1.0`, `true_positives == len(gold)`, `false_positives == false_negatives == 0`.
- **A corrupted extraction lowers the score (the SPEC §7 metric-integrity test).** Flip one
  candidate's CCMI (so `matches` fails) → recall (and/or precision) is **strictly less** than the
  clean run. The metric must move — proves it is not trivially always-1.0.
- A hallucinated pair is a false positive — a candidate not in the gold set → `false_positives`
  increments, `precision < 1.0`.
- A missed pair is a false negative — a gold rule with no matching candidate → `false_negatives`
  increments, `recall < 1.0`.
- Both arms are reported as a delta — `evaluate()` over `[direct, chroma]` → two `TrackAResult`s with
  the correct `retriever_name`s.

Track B:
- The fixture adjudicates 100% — every curated `(claim, expected)` case matches `adjudicate` →
  `accuracy == 1.0`, `n_correct == n_claims`, `confusion` only on the diagonal (expected==predicted).
- A wrong expectation is caught — a case whose expected status differs from the engine output →
  `accuracy < 1.0` and an off-diagonal key (e.g. `"expected=pay,predicted=deny"`) appears (tests the
  metric, not the engine).
- Track B is model-free — the Track B scoring path uses only `adjudicate`; no model import is reached.
- The `expected_decision` → status mapping is exact (hermetic — pass the category strings directly):
  `DENY_COLUMN_TWO` / `DENY_COLUMN_TWO_MODIFIER_NOT_ALLOWED` → DENY; `ALLOW_WITH_MODIFIER_REVIEW` →
  FLAG; `ALLOW_DIFFERENT_DATE` / `ALLOW_DIFFERENT_BENEFICIARY` / `ALLOW_NO_ACTIVE_PTP_EDIT` /
  `ALLOW_INACTIVE_EDIT_FOR_DOS` → PAY.
- Missing-context cases are excluded, not mis-scored — an `UNCERTAIN_REVIEW_REQUIRED` row is counted
  separately and never folded into `accuracy`.

Report shape:
- `EvalReport` carries `ruleset_version`, one `TrackAResult` per arm, and a `TrackBResult`.
- Zero-division is safe — an arm/example with no predictions yields `precision == 0.0` (documented
  convention, §6.3), not a crash.

## 5. Definition of done (SPEC §6, Phase 5 row)

`make test` green proving: **Track A computed per arm; Track B 100% on the fixture; a corrupted
extraction lowers the Track A score** (the metric itself is tested). Plus: the Track B path is
model-free (grep), `ruff` clean, both reviewers sign off, and **no metric/prompt tuning toward an
inflated Track A** (CLAUDE.md §6, SPEC §9). The real `make eval` is a separate manual run.

## 6. Open questions (explicit assumptions)

1. **Gold set — RESOLVED (2026-06-30).** `data/ccipra-v322r0-f1/ncci_ptp_goldset_100.xlsx` (100 rows,
   built from the v322r0 table) is the **Track B** fixture: each row = source PTP rule + a synthetic
   claim + a labeled `expected_decision`. Its `source_column_1/2` + `modifier_indicator` also serve as
   **Track A**'s authoritative pairs. **DECIDED (2026-06-30, ADR-012): Track A scores against the 100
   gold rows, NOT the full table** — full-table-as-gold makes recall meaningless against a manual that
   names few specific pairs. Expect a **low Track A recall** (gold pairs rarely appear verbatim in the
   manual prose); that is honest and expected (CLAUDE.md §6), and the eval report says so.
2. **Track B scoring — DECIDED (2026-06-30): deterministic exact-match, NO LLM judge.**
   `expected_decision` → expected per-line `pay/deny/flag` via a fixed mapping; `adjudicate` runs;
   statuses are exact-compared. Claim-level `n_correct`/`accuracy` (a claim is correct iff *every*
   line matches), line-level `confusion`. A model judge is rejected: the truth is a discrete labeled
   enum, so a judge only adds non-determinism + crosses the seam (DECISIONS ADR-010, SPEC §7,
   CLAUDE.md §2/§6).
3. **Precision/recall zero-division.** **Assumption:** `precision = 0.0` when there are no
   predictions, `recall = 0.0` when there is no gold; `f1 = 0.0` when either is 0. Documented + pinned
   by a test. Confirm.
4. **`run_eval()` output.** **Assumption:** `__main__` prints a human summary + the `EvalReport` as
   JSON to stdout; no file written (a reviewer can redirect). Confirm whether `eval/report.json` is
   wanted.
5. **Real run prerequisites.** `run_eval()` needs `data/` + a running Ollama + `ANTHROPIC_API_KEY`;
   it is the manual `make eval`, never in `make test` (hermetic via injection). Confirm.
6. **Depends on the Phase 4 duplicate-pair fix.** Before `run_eval()` feeds the **real** 675k-row
   table to `adjudicate` (Track B real run), the engine's duplicate-`(col1,col2)` lookup must be
   hardened (Phase 4 review outcome / DECISIONS open items). Track B on the hand-built fixture is
   unaffected.
7. **UNCERTAIN cases — DECIDED: exclude + report separately.** The 5 `UNCERTAIN_REVIEW_REQUIRED`
   (missing-context) rows have no engine equivalent (`DispositionStatus` is `pay/deny/flag`) and
   wouldn't build a valid `Claim`. Deterministic Track B scores the **95 adjudicable** cases (target
   100%); the 5 are counted + reported separately by `run_eval`'s `__main__` as out-of-engine-scope /
   routed-to-human-gate. (`TrackBResult` has no field for the excluded count — surfacing it *inside*
   the report would be a user-owned schema task; for now it is logged.)
8. **Different-beneficiary cases — DECIDED: model as separate claims.** `ALLOW_DIFFERENT_BENEFICIARY`
   rows can't sit in one `Claim` (single `beneficiary_id`); the loader builds two single-line claims
   → each PAYs. Correct fixture construction, no schema change.
9. **Gold-set loader dependency.** The `.xlsx` needs `openpyxl` (add to deps for the eval path) **or**
   a stdlib `zipfile` reader. `make test` never reads it (hermetic, hand-built fixture); only the real
   `make eval` does. Confirm `openpyxl` vs zipfile in review.

---

**Scope guard (Phase 5):** measurement only — no new schema fields, **no model on the Track B
path**, no tuning toward a flattering number. Track A reports both arms and the delta honestly; a
suspiciously high Track A is a red flag, not a win (CLAUDE.md §6, SPEC §9).

---

# Phase 5 — review outcome & required changes

Two Opus reviewers ran against the on-disk eval (`make test` = 71 passed, `ruff` clean, seam grep
empty going in). The real Track B path was run model-free: **105/105 = 1.0**, diagonal-only confusion.

**Verdict: code GREEN-with-nits; tests CHANGES-REQUIRED (shared with Phase 4) → NOT yet signed off.**

- **Code (`run_eval.py`, `evaluation/__init__.py`) — GREEN-with-nits.** Correct, honest, seam-clean.
  `score_track_b` and `adjudicate` reach no model/embedding/randomness (the Anthropic + Chroma wiring
  is confined to the Track A path); every non-PAY disposition cites `rule_id` + `ruleset_version`;
  Track B = deterministic exact-match (ADR-010). Track B 105/105 is **real, not masked** — gold set
  has 0 duplicate pairs and `_dedupe_candidates`/greedy matching collapse only identical pairs.
- **The honest number stands.** Track A scopes to the 100 gold rows (ADR-012); expect low recall.

### Code fixes (Codex — `policyforge/evaluation/run_eval.py`)

1. **SHOULD — build the ruleset only from `is_source_ptp_pair==1` rows.** `_rules_from_gold_rows`
   currently fabricates a `PTPRule` from **all** 100 rows, incl. the 10 `is_source_ptp_pair==0`
   (`ALLOW_NO_ACTIVE_PTP_EDIT`) rows — e.g. a non-edit `(0054T,0213T)`. Harmless on this gold set (0
   of those fabricated pairs collide with a billed pair; Track B still 105/105) but
   cross-contamination-prone. `ALLOW_NO_ACTIVE_PTP_EDIT` already works via `line2_code !=
   source_column_2`, so it needs no fabricated rule. → **verify:** ruleset length == count of
   `is_source_ptp_pair==1` rows; Track B still 105/105.
2. **SHOULD — `score_track_b` should not KeyError / silently untally on mismatched line sets.**
   `:99-104` uses `set(predicted) == set(expected)` (near-dead on aligned fixtures) and
   `predicted[line_id]` (KeyErrors if `expected` names a line the engine didn't return). Iterate the
   **union** of keys with `.get()`; count a missing/extra line as a mismatch + an explicit confusion
   key. → **verify:** a hand-authored case whose `expected` omits/adds a line scores incorrect, no crash.
3. **NIT (latent) — XLSX robustness.** `:330` `max(cells)` raises on an empty `<row>`; `:333`
   `dict(zip(headers, values))` truncates when a row omits trailing empty cells. Neither bites the
   current gold file. Pad rows to `len(headers)`; skip/`default=0` empty rows.

### Test fixes (Codex — `tests/`)

4. **REQUIRED — see Phase 4 re-review #1** (the `rule_id` tie-break test that does not bite). Shared
   blocker; lands with the engine dup-pair test.
5. **SHOULD — mainline gold-row → `adjudicate` round-trip.** Only the UNCERTAIN and
   different-beneficiary branches are exercised; the standard 2-line path and `_modifiers` parsing are
   not. Add: `_claim_cases_from_gold_rows([_gold_row(expected_decision="ALLOW_WITH_MODIFIER_REVIEW",
   modifier_indicator="1", line2_modifiers="59")])` → one 2-line claim whose L2 carries `59`, then
   `score_track_b` → `accuracy == 1.0`, FLAG on the diagonal. Add a `DENY_COLUMN_TWO` round-trip too.
6. **SHOULD — `score_track_a` greedy one-to-one matching.** Two identical candidates + one gold rule
   → `true_positives == 1`, `false_positives == 1`, `recall == 1.0`, `precision == 0.5` (guards
   against double-counting one gold rule).

### Report note (Track A scope, ADR-012)

`run_eval`'s `__main__` should state that Track A is scored against the 100 gold rows and that a low
recall is expected (gold pairs rarely appear verbatim in the manual prose) — so the number reads as
honest, not broken.

### Sign-off (2026-06-30) — GREEN-with-nits → SIGNED OFF

Codex landed code fixes #1 (PTP-only ruleset), #2 (`score_track_b` union-key safety), #3 (XLSX
robustness) and the Track A report note, plus tests #5 (gold-row round-trip) and #6 (greedy
one-to-one). `make test` = **76 passed**, `ruff` clean. The scoped re-review (Opus ×2) confirmed all
six changed/new tests are **behavioral** — each **fails under a targeted implementation mutation**
(tie-break, dup-pair, greedy 1:1, union-key safety, PTP filter, round-trip), verified by mutation.
The Track B path was re-run model-free: **105/105**, unchanged.

**Phase 5 is signed off (2026-06-30).** TESTING.md §7 covers it.

---

# Phase 6 — Gate + store (planned)

> Implements the Phase 6 row of SPEC §6: the **human gate** (`LangGraph interrupt`) and the
> **versioned rule store** (`SQLite + JSON Logic`). This is the *only* place LLM output crosses into
> the authoritative side — and it crosses **through a human** (SPEC §3 box 6, ADR-001). Extraction
> (Phase 3) produces `RuleCandidate` drafts; a human approves or rejects each one; approved ones
> become `PTPRule`s in the store; the engine (Phase 4) reads the store's ruleset to adjudicate.
> The store is also seeded with the authoritative table rules (SPEC §3: both `G` and the gate feed
> `S`). Depends on Phase 3 (candidates) and the `PTPRule` contract; consumed by Phase 7.

## 1. Objective

Stand up a human checkpoint and a versioned, auditable rule store such that **no candidate becomes a
live rule without an explicit human decision** (the DoD: "unapproved candidate never reaches store"),
every stored rule carries its provenance, and the store hands the engine authoritative `PTPRule`s —
with no model, embedding, or randomness on the store-read / adjudication path.

## 2. Interfaces

Two new modules. **No new `schemas.py` fields** — the store persists the existing `PTPRule` contract
plus approval/provenance as **SQLite sidecar columns** (locked decision, CLAUDE.md §3). A gate-layer
`GateDecision` enum and an `ApprovalRecord` row are *storage/orchestration* types local to these
modules, **not** parallel contract shapes for data that flows between phases (AGENTS.md §2) — flagged
explicitly so reviewers don't read them as schema drift.

`policyforge/gate.py` — the pure decision logic + the LangGraph interrupt node:

```python
class GateDecision(str, Enum):           # gate-layer, NOT a schemas.py contract
    APPROVE = "approve"
    REJECT = "reject"
    # EDIT_APPROVE = "edit_approve"  -> future advancement (§6.2); NOT built in the demo scope

def review_candidate(
    candidate: RuleCandidate,
    decision: GateDecision,
    *,
    effective_date: date,                 # human supplies what prose can't (see §6.3)
    deletion_date: date | None = None,
    in_existence_prior_1996: bool = False,
) -> PTPRule | None: ...                  # REJECT -> None; APPROVE -> a validated PTPRule

def gate_node(state) -> dict: ...         # LangGraph node: interrupt() to surface the candidate,
                                          # resume with the human decision, write via the store
```

- **`review_candidate` is the seam guarantee.** `REJECT` returns `None` (the candidate never reaches
  the store). `APPROVE` builds a `PTPRule` from the candidate's extracted fields + the human-supplied
  date(s), **constructed through `PTPRule(...)`** — so an inconsistent approval (e.g. `deletion_date`
  before `effective_date`) raises `ValidationError` and nothing is written. No silent coercion, no
  try/except swallow. (The candidate's codes / CCMI already passed `RuleCandidate` validation upstream.)
- **No autonomous denial** (guardrail): `gate_node` never auto-approves and never auto-rejects; an
  un-reviewed candidate stays pending at the `interrupt()` and is **not** dropped.

`policyforge/store.py` — the versioned SQLite store:

```python
class RuleStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None: ...
    def seed_authoritative(self, rules: list[PTPRule], *, ruleset_version: str) -> None: ...
    def add_approved(self, rule: PTPRule, candidate: RuleCandidate, *,
                     ruleset_version: str, approver: str, approved_at: datetime) -> None: ...
    def load_ruleset(self, ruleset_version: str) -> list[PTPRule]: ...   # what the engine reads
    def versions(self) -> list[str]: ...
    def provenance_for(self, rule_id: str, ruleset_version: str) -> dict: ...   # for the UI
```

- **Sidecar columns** per stored rule: the eight `PTPRule` fields (so the row reconstructs a
  `PTPRule` on read), `rule_id`, `json_logic` (TEXT — the `to_json_logic()` output, the SPEC's
  "JSON Logic" half), `ruleset_version`, `origin` (`'authoritative' | 'human_gated'`), and for
  human-gated rows the provenance: `approver`, `approved_at`, `source_chapter`, `source_quote`,
  `extraction_confidence`, and `quote_grounded` (ADR-005 low-trust flag). (The edit-and-accept
  advancement, §6.2, adds `decision` / `edited` / `original_candidate_json` columns; the demo build
  omits them — vacuous under approve/reject, AGENTS.md §1.)
- **`load_ruleset` reconstructs through `PTPRule(...)`** so the engine always receives validated
  authoritative shapes — the store-read is the gate on the way out, just as `PTPRule(...)` is on the
  way in. Returns exactly the rules stamped with that `ruleset_version`.
- The store performs **no model/embedding call and no randomness**; `approved_at` is **passed in**
  (injected) so tests are deterministic and nothing time-dependent sits on a scored path.

## 3. Steps (each with its verify line)

1. **`RuleStore` schema + seed.** Create the SQLite table (sidecar columns above); `seed_authoritative`
   writes table rules with `origin='authoritative'`; `load_ruleset` reconstructs `PTPRule`s.
   → **verify:** `pytest tests/test_gate.py -k store_roundtrip` green — a seeded rule loads back equal
   (codes / CCMI / dates / `json_logic`), so the engine reads it unchanged.
2. **`review_candidate` decision logic.** Implement REJECT→`None` and APPROVE (built through
   `PTPRule(...)`).
   → **verify:** `pytest tests/test_gate.py -k review` green (scenarios in §4), incl. the
   inconsistent-approval `ValidationError` case.
3. **`add_approved` writes provenance; reject writes nothing.** Wire `review_candidate` → `add_approved`;
   reject is a no-op against the store.
   → **verify:** `pytest tests/test_gate.py -k unapproved_never_reaches_store` green (the SPEC §6 DoD).
4. **LangGraph `gate_node` with `interrupt()`.** Thin wrapper: surface the candidate, pause, resume with
   the human decision, then write. Hermetic — tested with an in-memory checkpointer and a **canned
   resume value** (no real human, no network).
   → **verify:** `pytest tests/test_gate.py -k interrupt` green — the node pauses before writing; an
   `approve` resume writes one rule, a `reject` resume leaves the store empty.
5. **Seam + hygiene check.** No model / embedding / Chroma / randomness import in `gate.py` or
   `store.py` (`langgraph` and stdlib `sqlite3` are allowed — neither is a model nor a non-deterministic
   adjudication input).
   → **verify:** `grep -nE "anthropic|chromadb|embedding|random" policyforge/gate.py policyforge/store.py`
   is empty; `make lint` clean.
6. **Phase gate.** → **verify:** `make test` green.

## 4. Behavioral test expectations (Codex turns each sentence into a named test)

Hermetic: `RuleStore(":memory:")`, hand-built `RuleCandidate`/`PTPRule` fixtures, an in-memory LangGraph
checkpointer, a fixed injected `approved_at`. **No network. No smoke** — "it ran / returned non-null"
never counts.

- **An unapproved candidate never reaches the store (the SPEC §6 DoD).** `review_candidate(c, REJECT)`
  returns `None` and the store row count is unchanged; `load_ruleset` does not contain that pair.
- **An approved candidate becomes a loadable, adjudicable rule.** `APPROVE` (with a human
  `effective_date`) writes one row; `load_ruleset(v)` returns a `PTPRule` equal to the candidate's pair
  + CCMI; feeding it to `adjudicate` denies/flags the column-2 line citing that `rule_id`.
- **An inconsistent approval is rejected by validation (the seam holds on construction).** `APPROVE`
  with `deletion_date` before `effective_date` raises `ValidationError`; nothing is written — a rule
  exists only if it is a valid `PTPRule`.
- **The store round-trips through `PTPRule` on load.** A row written then read yields a `PTPRule`
  whose `is_active_on` / `to_json_logic` behave identically to the original (the engine reads
  authoritative shapes, never raw dicts).
- **The store is versioned.** Rules approved under `v2` are returned by `load_ruleset("v2")` and absent
  from `load_ruleset("v1")`; `versions()` lists both.
- **Authoritative and human-gated rules coexist in one ruleset.** After `seed_authoritative([...], v)`
  + an `add_approved(..., v)`, `load_ruleset(v)` contains both, and `provenance_for` reports
  `origin='authoritative'` vs `'human_gated'` (SPEC §3: `G` and the gate both feed `S`).
- **Provenance is recorded for the gate/UI.** An approved human-gated rule's `provenance_for` carries
  `approver`, `source_quote`, `extraction_confidence`, and `quote_grounded`; an ungrounded candidate
  (ADR-005) is stored with `quote_grounded=False` — surfaced as a low-trust flag, **not** auto-rejected.
- **The LangGraph gate interrupts before writing.** With an in-memory checkpointer, the graph pauses at
  `gate_node`; resuming with `approve` writes one rule, resuming with `reject` leaves the store empty
  (the human-in-the-loop checkpoint actually gates the write).

## 5. Definition of done (SPEC §6, Phase 6 row)

`make test` green proving **an unapproved candidate never reaches the store**, an approved one becomes a
loadable+adjudicable `PTPRule` **constructed through `PTPRule` validation** (an inconsistent approval is
rejected), and the store round-trips authoritative shapes. Plus:
**no model/embedding/randomness import in `gate.py` or `store.py`** (grep-verified), `ruff` clean, and
both reviewers sign off (every line traces to the gate/store goal; no autonomous denial; provenance
holds; tests behavioral, not smoke).

## 6. Open questions (explicit assumptions)

1. **Store persistence shape — DECIDED (2026-06-30, locked): SQLite sidecar columns, no schema change.**
   Provenance lives as the columns listed in §2; the candidate's grounding/confidence are columns, not a
   new `RuleCandidate`/`PTPRule` field. A typed `StoredRule`/`RuleApproval` schema type was the
   alternative — deferred to a possible user-owned, separately-reviewed schema task (CLAUDE.md §3).
2. **Gate actions — DECIDED (2026-06-30, locked): approve / reject for the demo build; edit-and-accept
   is a documented future advancement.** The demo gate is the cleanest test of "human gates the diff":
   `APPROVE` (candidate becomes a `PTPRule` via `PTPRule(...)`) or `REJECT` (never reaches the store);
   **no autonomous denial** (a human must decide; pending candidates are not dropped). The
   **edit-and-accept advancement** — a human fixes a wrong CCMI / code / rationale before approving,
   recorded via `decision` / `edited` / `original_candidate_json` columns + an `edits` arg on
   `review_candidate`, still constructed through `PTPRule(...)` — is **out of the demo build scope**
   (AGENTS.md §1). It directly serves the thesis that the human catches the 20–35% the LLM gets wrong
   (SPEC §9), so it is the recommended first post-demo addition.
3. **A candidate has no `effective_date` — the human supplies it (design consequence, confirm).**
   `RuleCandidate` (prose-derived) carries no date, but `PTPRule.effective_date` is required. The human
   supplies `effective_date` (and optional `deletion_date` / `in_existence_prior_1996`) at approval —
   the authoritative bit the LLM structurally cannot extract from prose. This is the human gate *adding*
   value, not a schema gap.
4. **Versioning scheme — assumption: caller-supplied `ruleset_version` label, no auto-increment.** The
   store stamps each seeded/approved rule with the version string the caller provides (e.g. the loaded
   table version `"v322r0"` or a gate-batch tag); `load_ruleset(version)` returns that version's rules.
   The engine's `ruleset_version` (ADR-002) is exactly this label. Confirm whether an auto-derived
   version (content hash / monotonic) is wanted instead.
5. **DB location — assumption:** `POLICYFORGE_STORE_PATH` env (default `./data/policyforge_store.db`,
   gitignored); tests use `:memory:`. Confirm.
6. **Retry cap of two (guardrail) — out of Phase 6 scope; honored in Phase 7 orchestration** (it governs
   extraction retries, not the gate). Flagged here for traceability; see Phase 7 §6.4.

---

**Scope guard (Phase 6):** gate + store only — no UI (Phase 7), no new schema fields (sidecar columns
only), no model/embedding/randomness on the store-read or adjudication path. The gate completes &
validates candidates into `PTPRule`s **through the contract**; it never adjudicates and never decides
autonomously. SPEC §2 out-of-scope (auth, multi-user) stays out — `approver` is a recorded string, not
an auth system.

---

# Phase 7 — Orchestrate + UI (planned)

> Implements the Phase 7 row of SPEC §6: the **LangGraph orchestration graph** that chains Phases 1–6,
> and **3 Streamlit views** over it (SPEC §3: `S` and `D` feed the UI). The DoD is `make demo` running
> **end-to-end on one real code pair**. This phase *composes* — it adds no new decision logic, no
> schema fields, and keeps the seam intact (the model appears only on the conversion nodes, never on the
> adjudication node). Depends on Phases 1–6.

## 1. Objective

Wire the full pipeline — **ingest → retrieve → extract → human gate → store → adjudicate** — as a
LangGraph graph, and present it through three Streamlit views (**Pipeline / Gate / Adjudicate**, locked)
so a reviewer can watch policy prose become a draft rule, gate it, and see a claim adjudicated with a
fully cited disposition. `make demo` proves the whole thesis on one real code pair.

## 2. Interfaces

Two new surfaces. **No new `schemas.py` fields.** The graph's working state is a **graph-local
`TypedDict`** composing existing contract types (code pair, chapter text, `PolicyChunk`s,
`RuleCandidate`s, gate decisions, `Claim`, `ClaimDisposition`, `ruleset_version`) — orchestration state,
not a contract shape (flagged for reviewers, AGENTS.md §2).

`policyforge/orchestration.py` — the LangGraph graph + its builder:

```python
def build_demo_graph(*, retrievers, extract_fn, store, checkpointer, clock) -> CompiledGraph: ...
def run_demo() -> None:   # production entry: wires real deps (Phase 1 data, Phase 2 arms,
                          # real extract_rules, RuleStore on disk) — invoked by `make demo` UI
```

- **Nodes:** `ingest` (Phase 1 `load_policy_sections` / `load_ptp_table`, seeds the store) →
  `retrieve` (Phase 2 arm) → `extract` (Phase 3 `extract_rules` + `is_quote_grounded`) →
  `gate` (Phase 6 `gate_node`, **interrupt**) → `adjudicate` (Phase 4 `adjudicate` over
  `store.load_ruleset(version)`).
- **Injection mirrors Phase 5's `evaluate()`** (DI is the testability seam): the builder takes the
  retriever arms, an `extract_fn`, the `store`, a `checkpointer`, and a `clock` (for `approved_at`), so
  `make test` drives the graph with deterministic fakes and **no network**; `run_demo()` wires the real
  deps. The **`adjudicate` node reaches only `adjudicate` + the store** — no model/embedding/randomness.

`app/main.py` (+ `app/views/`) — the Streamlit shell, three views over the graph/library:

- **View 1 — Pipeline** (the conversion trace): for a chosen code pair, show the ingested **chapter
  prose**, **which retriever arm served it and what it returned** (the Phase 2 `direct`-vs-`chroma`
  ablation — chunks, `score`, chapter provenance), and the **extracted candidates** (CCMI, confidence,
  `source_quote`, grounded flag). "Show your work" for the left half of the seam.
- **View 2 — Gate** (the human checkpoint): list candidates; per candidate **approve / reject** (supply
  `effective_date` on approve), with the low-trust (ungrounded / low-confidence) flag shown; approve
  writes to the **versioned store** (Phase 6), reject discards. Shows the current `ruleset_version` and
  what's been approved. (Edit-and-accept is a documented future advancement — Phase 6 §6.2.)
- **View 3 — Adjudicate** (the auditable output): enter/select a `Claim` (lines: code, units, modifiers,
  DOS), run `adjudicate` against the store's current ruleset, and show the per-line **disposition** —
  `status`, `reason_code`, `cited_rule_id`, `ruleset_version`, `explanation` — with a drill-in to the
  cited rule's `json_logic` + provenance.

`make demo` (`streamlit run app/main.py`) is the **manual** end-to-end run (needs `data/` + Ollama +
`ANTHROPIC_API_KEY`, like `make eval`); it is **not** part of `make test`.

## 3. Steps (each with its verify line)

1. **Build the graph.** Assemble the five nodes with the gate `interrupt()` between `extract` and
   `adjudicate`, over injected deps.
   → **verify:** `pytest tests/test_orchestration.py -k builds` green — the compiled graph exposes the
   node order ingest→retrieve→extract→gate→adjudicate.
2. **End-to-end via fakes (approve path).** Drive the graph with a fake `extract_fn` (canned candidate),
   in-memory store + checkpointer, and an `approve` resume.
   → **verify:** `pytest tests/test_orchestration.py -k end_to_end_approve` green — the run yields a
   `ClaimDisposition` whose non-PAY line cites the approved `rule_id` + the store's `ruleset_version`.
3. **Reject gates the pipeline.** Same graph, a `reject` resume.
   → **verify:** `pytest tests/test_orchestration.py -k reject` green — the candidate never enters the
   store and the claim **PAYs** (the gate gates the *whole pipeline*, not just the DB write).
4. **Streamlit shell.** `app/main.py` with the three views calling the library/graph; `run_demo()` wires
   real deps.
   → **verify (manual):** `make demo` launches, and on one real code pair walks Pipeline → Gate →
   Adjudicate to a cited disposition (recorded in TESTING.md §8 as a manual check, not in `make test`).
5. **Seam + hygiene check.** Model/embedding use is confined to the `retrieve`/`extract` nodes; the
   `adjudicate` node and `store` read reach no model/embedding/randomness; the UI re-implements no
   decision.
   → **verify:** `grep -nE "anthropic|chromadb|embedding|random" policyforge/orchestration.py` shows
   such use only in the conversion nodes, never the `adjudicate` node; `make lint` clean.
6. **Phase gate.** → **verify:** `make test` green (the orchestration graph tests; the UI is a manual
   `make demo`).

## 4. Behavioral test expectations (Codex turns each sentence into a named test)

The **Streamlit UI is a thin shell and is not unit-tested** (its logic is the already-tested library);
coverage lives in the **orchestration graph** tests + the manual `make demo`. Hermetic: fake
`extract_fn`, in-memory `RuleStore` + checkpointer, injected `clock`. **No network. No smoke.**

- **The pipeline runs end-to-end through an approval.** A fake candidate for a CCMI-0 pair, an `approve`
  resume → the final `ClaimDisposition` denies the column-2 line, citing the approved `rule_id` and the
  store's `ruleset_version` (the full thesis: draft → gate → enforce, in one run).
- **A rejected candidate changes the outcome.** Identical graph + claim, a `reject` resume → the pair is
  absent from the store and the claim **PAYs** — proving the gate is load-bearing, not decorative.
- **The graph interrupts before adjudicating.** The `adjudicate` node does not execute while the
  candidate is still pending at the gate `interrupt()` (human-in-the-loop ordering — the engine never
  acts on an un-gated rule).
- **The adjudication leg is deterministic.** Given the same store + claim, two runs of the
  `adjudicate` node produce identical `LineDisposition` tuples (the seam holds through the graph).
- **The adjudicate node is model-free.** The Track-B-style path inside the graph reaches only
  `adjudicate` + the store; no model/embedding import is reached from the `adjudicate` node.

## 5. Definition of done (SPEC §6, Phase 7 row)

`make test` green with the orchestration graph tests (end-to-end approve; reject-gates-the-pipeline;
gate-before-adjudicate ordering; adjudicate-leg deterministic + model-free). **`make demo` runs
end-to-end on one real code pair** (manual; documented in TESTING.md §8). `ruff` clean. Both reviewers
sign off: the graph traces to the SPEC §3 flow, the **UI crosses no seam** (it only calls library
functions and re-implements no decision), no schema field is invented, and every demo disposition is
auditable (cites `rule_id` + `ruleset_version`). SPEC §2 out-of-scope (auth, multi-user, deployment,
scaling) stays out.

## 6. Open questions (explicit assumptions)

1. **3 views — DECIDED (2026-06-30, locked): Pipeline / Gate / Adjudicate.** View 1 surfaces the Phase 2
   `direct`-vs-`chroma` ablation and the conversion trace; Views 2–3 are the gate and the cited
   disposition. (Note: the live ablation in View 1 needs a running Ollama for the `chroma` arm; the
   `direct` arm works offline.)
2. **UI testing — DECIDED (2026-06-30): the Streamlit views are a presentation shell, NOT unit-tested**
   (ADR-007 spirit). Behavioral coverage is the orchestration graph + the already-tested library;
   `make demo` is the manual proof. `streamlit.testing` smoke runs were rejected — they would assert
   only "the view rendered," exactly the smoke-as-coverage the test rubric flags (SPEC §7).
3. **Graph state — assumption: a graph-local `TypedDict`** composing existing contract types, not a
   `schemas.py` type. Confirm.
4. **Retry cap of two (guardrail) — assumption: the `extract` node caps retries at two**; on repeated
   failure the candidate is surfaced as failed-to-extract (routed to the human), **never silently
   dropped**. Confirm whether to build the retry/backoff or only document the cap.
5. **Demo code pair + claim — DECIDED (2026-06-30): `11042/97597` (CCMI-1).** It is the richest single
   pair — the same pair shows **DENY** (no bypass modifier) *and* **FLAG** (column-2 line carries
   modifier `59`), exercising the most distinctive NCCI branch — and it is already the canonical pair
   across the Phase 1 / 3 / 4 fixtures, so the demo stays consistent. Two prebuilt sample `Claim`s
   (with / without `59`) make both outcomes reproducible. **Robustness:** if the real manual prose
   doesn't name the pair verbatim (ADR-012 — low Track A recall is expected), the demo still runs
   end-to-end because the store is seeded with the authoritative table rule for the pair and the
   `direct` arm serves the chapter; the gate approves the candidate the LLM did surface, else the
   seeded authoritative rule is adjudicated.
6. **Demo store — assumption:** `run_demo()` uses a demo `RuleStore` seeded from the authoritative table
   on first run (gitignored DB); it does not mutate any eval store. Confirm.

---

**Scope guard (Phase 7):** orchestration + UI only — composes Phases 1–6, adds no adjudication logic, no
schema fields, no model/embedding on the `adjudicate` node. The graph is the wiring; the UI is a
presentation shell that never re-implements a decision or crosses the seam. A suspiciously slick demo
that hides the human gate or the citations would defeat the point — the demo's job is to *show* the seam,
the gate, and the provenance, honestly (CLAUDE.md §2/§6).

---

# Phase 6 & 7 — review outcome & required changes

Two-reviewer pass (code correctness + BDD-vs-smoke) on the six built files: `policyforge/gate.py`,
`policyforge/store.py`, `policyforge/orchestration.py`, `app/main.py`, `tests/test_gate.py`,
`tests/test_orchestration.py`. **Verdict: APPROVE-WITH-NITS on both, zero blockers.** Confirmed clean:
the seam holds (seam grep empty; `adjudicate` node is model-free), construction-is-the-gate is honored
(APPROVE builds through `PTPRule(...)`; REJECT writes nothing), no autonomous-denial path, no schema
invention, no edit-and-accept leakage, deps injected. `ruff` clean; suite green.

### Fixes applied (2026-06-30)

1. **Audit timestamp reflected the pipeline, not the human.** The `extract` node stamped `approved_at =
   clock()` *before* the `interrupt()`, so the recorded approval time predated the human decision.
   Fix: `gate_node` now sets `approved_at` from the **resume payload** (the moment of decision), falling
   back to pipeline state; the `extract` node no longer stamps it. New biting test
   `test_gate_records_the_resume_timestamp_not_the_pipeline_time` (resume time ≠ pipeline time).
2. **DoD test couldn't fail.** `test_an_unapproved_candidate_never_reaches_the_store` and the
   inconsistent-approval test both asserted `load_ruleset() == []` after calling `review_candidate`,
   which never touches the store — a vacuous assertion. Fix: renamed the unit test to what it proves
   (`review_candidate_rejects_to_none_and_builds_no_rule`), dropped the vacuous lines, and added
   `test_gate_node_writes_nothing_when_an_approved_rule_fails_validation` (drives `gate_node`, so the
   empty-store assertion now bites). The store-level DoD is also covered by the reject-resume gate test.
3. **Direct-vs-chroma ablation was untested through Phase 7.** Every graph test used a single retriever.
   Fix: `FakeRetriever` takes a `name`; new `test_retrieve_records_both_arms_of_the_direct_vs_chroma_ablation`
   asserts both arms land in `retrieval_trace` with distinct chunks/scores. `_retriever_summary` now
   returns a structural `arm` ("control"/"treatment") flag, and the trace test asserts on that instead
   of brittle prose copy.

Also folded: removed a duplicate static-edge test, relaxed two over-tight UI-copy assertions, removed a
dead `hasattr` branch. Suite: **102 passed** (was 100), `ruff` clean, seam grep empty.

### Logged follow-ups (deferred — not blocking the demo)

- **Zero-candidate graph run `KeyError`s at the gate** (`orchestration.py` gate node reads `state["candidate"]`).
  Latent: never hit today (`make demo` uses `app/main.py`, which handles empty candidates; graph tests
  always return a candidate). Fails loud, not silent-wrong. Fix later with a conditional edge that skips
  the gate when extraction returns nothing (PLAN §6.5 robustness).
- **UI re-click inserts duplicate approved rows** (`app/main.py` `_gate_view`). Harmless to adjudication
  (append-only audit semantics; `provenance_for` uses `LIMIT 1`), but bloats the store. Add a uniqueness
  guard or pop the candidate after approval.
- **`_store()` re-opens the DB every Streamlit rerun** (no `@st.cache_resource`); `RuleStore` has no
  `close()`/context-manager. GC-bounded, wasteful only. Wrap in `@st.cache_resource`.
- **UI approval path duplicates gate logic** (`app/main.py` re-implements `review_candidate`→`add_approved`
  with `datetime.now()` rather than driving `gate_node`). Acceptable under "UI shell not unit-tested," but
  it is a second, untested write path — collapse onto `gate_node` in a later pass.
- **Minor:** `quote_grounded` defaults `True` when absent (mild ADR-005 low-trust tension; both real paths
  compute it); `provenance_for` returns `approved_at` as an ISO string, not a parsed `datetime`.
- **Chroma telemetry noise (Phase 2 dep defect, surfaced 2026-06-30).** With the `chroma` treatment arm
  enabled, `chromadb` 0.5.23 calls `posthog.capture()` with 3 positional args but `posthog` 7.21.0 accepts
  1, so each telemetry send logs a **non-fatal** `ERROR` ("capture() takes 1 positional argument but 3 were
  given"); `anonymized_telemetry=False` is ignored in this version. Retrieval is unaffected and the seam is
  untouched (Chroma is never on the adjudication path). **Mitigated now** by silencing the
  `chromadb.telemetry.product.posthog` logger in `build_chroma_index` (`retriever.py`). **Proper fix
  (deferred, needs a dependency change):** pin a compatible `posthog` (`<6`) in `requirements.txt`.

### Sign-off (2026-06-30) — APPROVE-WITH-NITS → SIGNED OFF

Both phases **SIGNED OFF**. Fixes landed (audit timestamp, DoD-test-bite, ablation coverage + folded
test nits); `make test` **102 passed**, `ruff` clean, seam grep empty across `gate.py` / `store.py` /
`orchestration.py`. The logged follow-ups above are tracked, non-blocking, and safe to defer past the
demo. TESTING.md updated (Phase 6 + 7 tables, total flipped to 102). With Phases 1–7 signed off,
PolicyForge is complete end-to-end; the remaining assessment artifacts (slides, video) are outside the
build.
