# DECISIONS.md — PolicyForge decision log

ADR-style record of the non-obvious choices made building PolicyForge, with rationale and the
alternatives considered. Source of truth for *design intent*; the contracts live in
`policyforge/schemas.py`, the phase plans in `PLAN.md`. Newest decisions may supersede older ones —
status is noted per entry.

---

## ADR-001 — The seam: the LLM converts, the engine decides, they never merge
**Date:** 2026-06-29 · **Status:** Accepted (foundational)

**Context.** PolicyForge adjudicates payment. A wrong rule shipped quietly is worse than a slow PR.

**Decision.** No model call, embedding, or non-deterministic step may sit on the adjudication path.
The LLM (Phase 3) only produces `RuleCandidate` *drafts*; a human gate (Phase 6) approves them; the
deterministic engine (Phase 4) decides claims, and every `deny`/`flag` cites a `rule_id` +
`ruleset_version`. The two halves meet only through the human gate and the rule store.

**Rationale.** Auditability and defensibility in a provider appeal. The reason the project exists is
to *avoid* "let the model also score the claim."

**Alternatives.** A single LLM that both extracts and adjudicates — rejected: unauditable,
non-deterministic, the exact failure mode this project guards against.

---

## ADR-002 — `adjudicate` takes `ruleset_version` as a parameter, not a `Ruleset` type
**Date:** 2026-06-29 · **Status:** Accepted (Phase 4) · PLAN.md Phase 4 §2

**Context.** `ClaimDisposition.ruleset_version` is required, but no `Ruleset` schema type exists, and
SPEC §6 writes the signature loosely as `adjudicate(claim, ruleset)`.

**Decision.** `adjudicate(claim: Claim, rules: list[PTPRule], ruleset_version: str) -> ClaimDisposition`.

**Rationale.** Smallest surface, no schema change; the caller already knows the version it loaded;
keeps Phase 4 in its lane. A schema change is its own reviewed task (CLAUDE.md §3), not a Phase 4
side effect.

**Alternatives.** Add a `Ruleset` schema type (`version` + `rules`) — cleaner long-term, deferred to
a possible future user-owned schema task.

---

## ADR-003 — Chroma treatment arm embeds via a local Ollama server (env-configured, injectable)
**Date:** 2026-06-29 · **Status:** Accepted (Phase 2) · PLAN.md Phase 2 §2/§6.1

**Context.** SPEC frames the treatment arm as semantic "vector search / embedding model," but a hard
guardrail forbids network egress except the Anthropic API.

**Decision.** Embed via a **local Ollama** server, configured from `POLICYFORGE_OLLAMA_BASE_URL`
(default `http://localhost:11434`) + `POLICYFORGE_EMBEDDING_MODEL`, pinned for reproducibility. The
embedding function is **injectable**.

**Rationale.** A localhost call is loopback, not external egress — consistent with the no-cloud
guardrail — and it honors the SPEC's semantic framing. Injection keeps the test suite hermetic.

**Alternatives.** Offline TF-IDF / hashing vectors (scikit-learn) — zero download, fully
deterministic, but only a *lexical* ablation, weaker than the SPEC's "embedding" intent. Rejected.

---

## ADR-004 — Retriever no-match semantics: treatment returns pure top-k
**Date:** 2026-06-29 · **Status:** Accepted (Phase 2) · PLAN.md "Phase 2 & 3 — review outcome" §C

**Context.** A code review found the Chroma arm post-filtered vector hits with a lexical AND-match,
collapsing the treatment into the control and breaking the ablation. Removing that gate means a kNN
index always returns neighbors, so the treatment can no longer return `[]` on an irrelevant query.

**Decision.** The treatment arm returns its **k nearest chunks by similarity** (no lexical gate,
no threshold). "Empty on no match" is a **control-only** invariant.

**Rationale.** "Top-k always" is the honest, knob-free representation of vector search; it keeps the
Phase 5 direct-vs-Chroma delta interpretable (attributable to retrieval quality, not a hidden
cutoff) and cannot silently suppress results.

**Alternatives.** Distance-threshold cutoff for symmetric empty-on-no-match — *betterment:* intuitive
"no relevant policy → nothing," trims Phase 3 token cost; *problem:* a tunable knob that confounds
the ablation if mis-set. Deferred; if adopted it is its own reviewed change with the cutoff pinned
and reported.

---

## ADR-005 — Extraction records ungrounded candidates; it does not drop them
**Date:** 2026-06-29 · **Status:** Accepted (Phase 3) · PLAN.md Phase 3 §3.3/§6.3

**Context.** The model may return a `RuleCandidate` whose `source_quote` is not actually in the
source text — a likely hallucination. Drop it, or keep it?

**Decision.** `extract_rules` **retains** ungrounded candidates as faithful model output and does
**not** overwrite the model's `extraction_confidence`. Grounding is exposed via a pure helper
`is_quote_grounded(candidate, text) -> bool` and reported as a hallucination-rate signal (Phase 5) +
a low-trust flag at the human gate (Phase 6).

**Rationale.** Surfacing hallucinations as a *measured number* is more honest than hiding them by
dropping (SPEC §9, "protect the honest number"). The ungrounded rate becomes evidence of system
performance.

**Alternatives.** Drop ungrounded candidates — quietly inflates precision by hiding model failures;
rejected. Persisting a `quote_grounded: bool` on `RuleCandidate` is an optional future schema task.

---

## ADR-006 — Extraction uses an injectable client + tool-use structured output; Pydantic is the gate
**Date:** 2026-06-29 · **Status:** Accepted (Phase 3) · PLAN.md Phase 3 §2

**Context.** Phase 3 is the one real model call. It must be reproducible, testable offline, and never
emit anything but validated candidates.

**Decision.** `extract_rules(text, source_chapter, *, client=<injected>, model=<env>)`. Structured
output via a tool whose `input_schema` mirrors `RuleCandidate`; every returned object is parsed
through `RuleCandidate(...)` so malformed output raises `ValidationError` (no coercion, no silent
skip). The model is pinned via `POLICYFORGE_EXTRACTION_MODEL`. The prompt is honest by construction
(verbatim quote required; no completing pairs from prior knowledge).

**Rationale.** Pydantic as the single validation gate; injection keeps tests hermetic; pinning keeps
eval numbers reproducible; honesty-by-construction protects Track A.

**Alternatives.** Free-text/JSON parsing with bespoke validation — rejected: forks the schema and
weakens the gate.

---

## ADR-007 — The automated test suite is hermetic; live services are opt-in integration tests
**Date:** 2026-06-29 · **Status:** Accepted (cross-cutting)

**Context.** Phases 2 and 3 touch external services (Ollama, Anthropic). `make test` must stay fast,
deterministic, and runnable in CI without secrets or a running server.

**Decision.** `make test` makes **no network call**: the embedding function and the Anthropic client
are injected with deterministic fakes; any real-service test is a separate integration test that
`skip`s when the service/key is unavailable.

**Rationale.** A green bar that depends on a local server or a paid API is neither honest nor
portable. Behavioral coverage of the *logic* needs no live call.

**Alternatives.** Hitting real services in unit tests — rejected: flaky, slow, non-portable, costs
tokens.

---

## ADR-008 — Data + network provenance: downloads are out-of-band; only Anthropic egress is allowed
**Date:** 2026-06-29 · **Status:** Accepted (cross-cutting) · SPEC §10

**Context.** The CMS NCCI files sit behind an AMA-CPT click-through; the guardrail forbids network
egress except the Anthropic API.

**Decision.** Real data is downloaded by a human, out-of-band, into `data/` (gitignored, never
committed); no code path fetches it (`make fetch-data` only prints URLs). The only external network
call any code makes is to the Anthropic API (extraction). Ollama is localhost loopback.

**Rationale.** Honors licensing and the egress guardrail; keeps the repo free of licensed data.

---

## ADR-009 — Phase 4 engine adjudication semantics
**Date:** 2026-06-30 · **Status:** Accepted (Phase 4) · PLAN.md Phase 4 §3/§4/§6

**Context.** Refining the deterministic engine plan against SPEC §5 surfaced four semantics that must
be explicit so the engine is auditable and the Track B fixture is unambiguous.

**Decisions.**
- *Same-DOS pairing.* An edit fires only between two claim lines with equal `date_of_service`
  (beneficiary + provider are claim-level constants). The same pair on different dates → both PAY.
- *Bypass-modifier location.* A CCMI-1 edit FLAGs only when the modifier sits on the **column-2
  (denied) line**; a modifier on the column-1 line does not bypass. Matches SPEC §5 literally.
- *Bypass-modifier set.* The pinned `NCCI_PTP_ASSOCIATED_MODIFIERS` constant (anatomic + global-
  surgery + 27/59/91 + X-modifiers) in `engine.py` — deterministic policy data, not a model input.
- *Most-severe-wins.* When a line is the column-2 of multiple active edits, `DENY > FLAG > PAY`,
  citing the producing rule, ties broken by `rule_id` sort.

**Rationale.** Each is the literal reading of SPEC §5 / NCCI PTP; encoding them as named behavioral
tests makes the engine's decisions defensible in a provider appeal and keeps Track B 100%-checkable.

**Alternatives.** Bypass modifier on either line (closer to looser CMS wording, more permissive) —
deferred in favor of the SPEC-literal column-2-only rule.

---

## ADR-010 — Track B is scored by deterministic exact-match, not an LLM judge
**Date:** 2026-06-30 · **Status:** Accepted (Phase 5) · PLAN.md Phase 5 §2/§4/§6

**Context.** A 100-row gold set (`data/ccipra-v322r0-f1/ncci_ptp_goldset_100.xlsx`, built from the
v322r0 table) labels each synthetic claim with a discrete `expected_decision`. The question arose
whether to score claim-level accuracy with an LLM-as-judge.

**Decision.** Score Track B by **deterministic exact-match**: map `expected_decision` → expected
per-line `pay/deny/flag`, run `adjudicate`, compare statuses. No LLM judge anywhere on Track B.
- The 5 `UNCERTAIN_REVIEW_REQUIRED` (missing-context) rows are **excluded** from engine accuracy and
  reported separately (the engine emits only `pay/deny/flag`; they route to the human gate).
- `ALLOW_DIFFERENT_BENEFICIARY` rows are modeled as **separate single-line claims** (`Claim` carries
  one `beneficiary_id`).

**Rationale.** Track B is the un-fakeable north star (SPEC §7); the truth is a discrete labeled
enum, so exact match is free, reproducible, and auditable. An LLM judge would re-introduce the very
non-determinism the deterministic engine exists to remove and would cross the seam (CLAUDE.md §2) —
a payment-integrity reviewer would not trust an LLM-judged correctness number (CLAUDE.md §6).

**Alternatives.** LLM-as-judge for claim accuracy — rejected (non-deterministic, un-auditable,
seam-crossing, and strictly worse than exact match against labeled truth). LLM-judge is reserved, if
ever used, for open-ended outputs in a clearly-labeled, non-scored qualitative layer.

---

## ADR-011 — Resolve duplicate `(col1,col2)` rows by date-of-service before adjudication
**Date:** 2026-06-30 · **Status:** Accepted (Phase 4/5) · supersedes the "defer to Phase 5" note in
PLAN.md "Phase 4 — review outcome"

**Context.** `engine.py:36` builds the rule lookup as `{(col1,col2): rule}` — a dict comprehension,
so a recurring pair keeps only the **last** row. Measured on the real v322r0 table
(`ccipra-v322r0-f1.TXT`): **675,037 rows / 603,624 distinct pairs; 70,898 pairs recur; 142,311 rows
(21.08%) belong to a duplicated pair**, with genuinely non-overlapping effective/deletion windows
(e.g. `(00100,99201)`: 1996-01-01…2019-12-31 **and** 2020-10-01…2020-12-31; `(0007U,0328U)`:
2023-01-01…2023-06-30 **and** 2023-07-01…active). The 100-row Phase 5 gold set has **zero** duplicate
pairs, and `run_eval` feeds the gold rules (not the table) to `adjudicate`, so the bug is **dormant**
for the eval as shipped (Track B = 105/105).

**Decision.** Harden `adjudicate` **now** (not deferred): group `rules` per `(col1,col2)`; at lookup,
select the row with `is_active_on(date_of_service)` True, deterministic tie-break by `rule_id` (or
latest `effective_date`). Add a behavioral test — same pair, two disjoint windows, claim DOS in the
active window → cites the active rule **regardless of list order**. The Phase 5 Track B number is
unchanged (no duplicate pairs in gold).

**Rationale.** It sits on the un-fakeable adjudication path; on real data the failure mode is a
**silent missed/wrong denial** (overpayment) — exactly the "wrong rule shipped quietly" this project
guards against (CLAUDE.md §2). The fix is ~6 deterministic, seam-clean lines.

**Alternatives.** Track as a known limitation until full-table wiring — rejected: cheap to fix, and
the silent-overpayment failure mode has no test to catch it if anyone later sets `rules=full_table`.

---

## ADR-012 — Track A scores against the 100 gold rows, not the full table
**Date:** 2026-06-30 · **Status:** Accepted (Phase 5) · PLAN.md Phase 5 §6.1

**Context.** PLAN §6.1's wording implied "Track A precision uses the full table." The implemented
`run_eval` scores extraction candidates against the **100 gold pairs** (`gold_examples`), and a code
review flagged the deviation.

**Decision.** Track A gold = the **100 gold rows**. Note in the eval report that Track A recall reads
**low** because gold pairs (table-derived codes) rarely appear verbatim in the policy-manual prose the
retriever serves; that low number is **expected and honest**, not a bug. PLAN §6.1 wording is corrected
to match.

**Rationale.** Full-table-as-gold (603k pairs) makes recall meaningless against a manual that names
few specific pairs; the 100-row gold is the interpretable, honest oracle (CLAUDE.md §6).

**Alternatives.** Full-table gold — rejected (recall → ~0, uninterpretable).

---

## Open / pending (not yet decided)
- **Engine within-bucket tie-break key is vacuous (`engine.py:65`, LOW — tracked at Phase 4/5 sign-off
  2026-06-30):** `min(active_rules, key=lambda r: r.rule_id)` keys on a value that is **identical for
  every row in a `(col1,col2)` bucket** (`rule_id` is derived from the pair), so multi-active selection
  silently degrades to **input-list order**, not rule data — ADR-011's promised "deterministic tie-break
  when multiple active rows remain" is not truly delivered. **Zero observable impact** on the gold set or
  the real v322r0 table: disjoint effective/deletion windows leave exactly one `is_active_on` survivor,
  and the cited `rule_id` is identical regardless of choice. It could only bite **two simultaneously-active
  rows in one bucket with different CCMI** — outside NCCI's sequential-versioning model, and no test covers
  that case. Both Opus re-reviewers flagged it; accepted as a follow-up, not a sign-off blocker. **Close by**
  keying on `(effective_date, deletion_date or date.max)` (latest-effective, ADR-011's own alternative) +
  one engine test with two simultaneously-active rows proving order-independence. Revisit if real data ever
  exhibits the shape.
- **Schema carry-overs (user-owned, CLAUDE.md §3):** `PTPRule.is_active_on` deletion-date boundary
  uses `>=` (confirm first-inactive vs last-active convention); `PTPRule.to_json_logic` emits an
  unconditional DENY for CCMI 9 (a deleted edit should not adjudicate). See PLAN.md §7b.
