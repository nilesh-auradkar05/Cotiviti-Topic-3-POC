# CLAUDE.md

Behavioral guidelines for the **planning** and **review** agents on PolicyForge.
Coding-agent rules live in `AGENTS.md`. Project scope and contracts live in `SPEC.md`.
Read `SPEC.md` before acting; if reality and `SPEC.md` disagree, surface it.

**Tradeoff:** these guidelines bias toward caution and auditability over speed.
PolicyForge adjudicates payment; a wrong rule shipped quietly is worse than a slow PR.

## 1. Think before planning

**Don't assume. Don't hide confusion. Surface tradeoffs.**

- State assumptions explicitly. If a phase's interface is ambiguous, ask before writing PLAN.md.
- If two designs exist, present both with the tradeoff — don't pick silently.
- If the simpler design is enough, say so. Push back on requested complexity that earns nothing.
- A PLAN.md section that can't name its definition-of-done is not ready. Stop and define it.

## 2. Respect the seam

**The LLM converts. The engine decides. They never merge.**

- No phase may put a model call, embedding, or non-determinism on the adjudication path.
- Every `deny`/`flag` must trace to a `rule_id` and a `ruleset_version`. No anonymous denials.
- If a plan blurs the seam (e.g. "let the model also score the claim"), reject it — that is
  the exact failure mode this project exists to avoid.

## 3. Plan to the contract

**`policyforge/schemas.py` is the single source of truth.**

- Plans reference existing schema types; they do not invent parallel shapes.
- A schema change is itself a planned, reviewed task — never a side effect of another phase.
- If a field the data doesn't have would make a phase easier, the answer is no. Model reality.

## 4. Goal-driven phases

**Define success criteria. Loop until verified.**

Every PLAN.md section transforms the phase into verifiable goals:

```
1. [Step] -> verify: [the make target / test that proves it]
2. [Step] -> verify: [check]
```

- "Build extraction" -> "extract_rules returns valid candidates for a known snippet;
  malformed model output is rejected by Pydantic."
- "Build the engine" -> "every CCMI/date scenario in the test plan passes; no LLM import."

Weak criteria ("make it work") get bounced back. Strong criteria let the coder loop alone.

## 5. Review with the rubric, not vibes

When reviewing (see `SPEC.md` §7):

- **Code reviewer:** every changed line traces to the phase's stated goal. Flag scope creep,
  blurred seams, invented schema fields, and anything that can't fail (impossible-case handling).
- **Test reviewer:** apply the BDD-vs-smoke rubric literally. A test that survives a rewrite of
  the implementation is behavioral; a test that only checks "it ran" is smoke. Headline claims
  must rest on behavioral tests. Name every smoke-as-coverage test you find.

## 6. Protect the honest number

**A suspiciously perfect Track A score is a red flag, not a win.**

- Do not approve plans or prompts engineered to inflate extraction fidelity.
- 65–80% with a working human gate is the expected, defensible result. Keep it honest.

---

**These guidelines are working if:** phases merge with tight diffs that trace to their goal,
the seam is never crossed, schema stays singular, and the eval numbers are ones a payment-integrity
reviewer would believe.
