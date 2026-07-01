# AGENTS.md

Rules for the **coding agent** on PolicyForge. You implement one phase at a time from
`PLAN.md`, against the contracts in `policyforge/schemas.py`, to the definitions of done
in `SPEC.md`. The planning/review agents follow `CLAUDE.md`.

**Tradeoff:** bias toward simplicity and verifiability over cleverness. This is a
weekend proof-of-concept that adjudicates payment — correct and legible beats impressive.

## 1. Build only the phase in front of you

- Implement exactly what the current PLAN.md section specifies. Nothing speculative.
- No features, abstractions, configurability, or "flexibility" that the phase didn't ask for.
- No error handling for impossible scenarios. If a case can't occur given the contract, don't guard it.
- If you write 200 lines where 50 would do, rewrite it. Ask: "would a senior engineer call this overcomplicated?"

## 2. Import the contract — never fork it

- All data shapes come from `policyforge.schemas`. Do not define a second `Rule`, `Claim`, etc.
- If you believe the schema is wrong, STOP and raise it as a separate task. Do not work around it
  with a local dataclass or a loose dict — that is how contract drift starts.
- Codes are 5-char HCPCS/CPT; modifiers are 2-char; CCMI is the `ModifierIndicator` enum. Use them.

## 3. Honor the seam

- Phase 4 (engine) and anything on the adjudication path: **zero** imports of the Anthropic SDK,
  embeddings, Chroma, or randomness. The engine is a pure function of `(claim, ruleset)`.
- The LLM (Phase 3) outputs `RuleCandidate` only. A candidate is never adjudicated against; it is
  scored (Track A) and gated (Phase 6). It becomes a `PTPRule` only after human approval.
- Every `deny`/`flag` line sets `cited_rule_id` and the disposition sets `ruleset_version`. No exceptions.

## 4. Tests are behavioral, not decorative

For every unit of behavior, write the test FIRST as a policy scenario, then make it pass.

- "Add the CCMI-0 path" -> first write `test_a_ccmi_zero_pair_denies_column_two_with_no_modifier`,
  watch it fail, then implement.
- A test must encode a rule a domain expert would recognize and must survive a from-scratch rewrite
  of the implementation. If it only asserts "function returns non-null", it is smoke — do not count it
  as coverage for a headline claim (engine correctness, Track A/B).
- Name tests as sentences describing the rule. Mirror the style in `tests/test_schemas.py`.
- Track B tests assert exact `pay`/`deny`/`flag` per line against the authoritative table. No fuzzing
  the expected answer to make a flaky test green.

## 5. Surgical changes

- Touch only what the phase requires. Don't reformat, rename, or "improve" adjacent code.
- Match existing style even where you'd differ.
- Remove imports/vars your change orphaned; leave pre-existing dead code alone (mention it instead).
- Every changed line should trace to the PLAN.md goal. The reviewer will check exactly this.

## 6. Verify before you hand off

A phase is done only when:

- The phase's `make` target is green (`make test`, plus `make eval` / `make demo` where named).
- `ruff check` is clean.
- No LLM/embedding import sits on the adjudication path (grep your diff).
- New behavior is covered by behavioral tests, not smoke.
- Provenance holds: dispositions cite rules; candidates carry source chapter + quote + confidence.

State a one-line plan before multi-step work:

```
1. [step] -> verify: [make target / named test]
2. [step] -> verify: [check]
```

---

**You are doing this right if:** diffs are small and traceable, the contract stays singular, the
seam is never crossed, and the test suite would catch a regression in the actual payment logic —
not just notice that the code still runs.
