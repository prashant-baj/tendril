# ADR-0007: Prompt/skill versioning across long-running tasks

**Status:** Accepted
**Date:** 2026-08-20
**Deciders:** Project owner / lead engineer

## Context

Tendril's engagements are **long-running and stateful** — a plan can span days to a full
harvest season (see `project-context.md`, ADR-0002). Prompts/skills are externalized and
versioned (ADR-0006) and can be updated *while plans are in flight*. This creates a real
tension:

- **Freshness** — improvements (better guidance, corrected/safer advice) should reach agents quickly.
- **Consistency** — a plan started under prompt v1 should not silently produce contradictory or incoherent advice when a follow-up three days later runs under v2.

A newly published version therefore *can* change the output of an in-flight plan — sometimes
beneficially (a knowledge/safety correction), sometimes harmfully (method/persona whiplash that
breaks plan coherence and user trust). The same problem exists for human advisors: knowledge
updates mid-engagement, and good professionals apply safety-critical updates immediately, keep the
current plan coherent otherwise, and communicate changes. That behavior is our north star.

## Decision

Separate two layers we had been conflating, and govern adoption with a layered policy:

1. **Fetch freshness** — how current the prompt an agent *can see* is (real-time / `DRAFT` / TTL, per ADR-0006). Relevant to **new** work and dev iteration.
2. **Per-plan adoption** — which version a *given plan* actually runs under. When a plan is created, the prompt/skill **version(s) are stamped into the plan's state** (DynamoDB, ADR-0002). A plan runs under its pinned versions **by default** for consistency, reproducibility, and auditability.

Layered on top, a **version-adoption policy** decides when an in-flight plan moves to a newer version:

- **Deterministic floor (non-negotiable):** every published change is classified. **Safety-critical** changes **force-apply** to all in-flight plans — no model discretion. **Cosmetic/method-only** changes are ignored for in-flight plans (applied to new plans only).
- **Intelligent tier (governance sub-agent):** the ambiguous middle (knowledge refinements) is adjudicated by a dedicated **change-governance sub-agent** (built from the same factory, ADR-0001). It receives the **semantic diff + change metadata**, the **plan's stage/state**, **relevance** to the plan, and **contradiction risk** with advice already given, and returns a **structured decision**: `adopt_now | adopt_at_milestone | keep_current | escalate`, with rationale and confidence.
- **Human-in-the-loop tier:** consequential adoptions are surfaced to the user for consent ("your guidance improved — apply to your current plan?") rather than applied silently (ADR-0001 interventions).
- **Audit:** every adoption decision, its rationale, and the version in force are stamped into the plan's capture-first event log (ADR-0002).

**Metadata requirement:** publishing a new prompt/skill version must include a **change type** (`safety` / `knowledge` / `method` / `cosmetic`) and a short rationale, so the deterministic floor can route and the governance sub-agent can reason.

## Options Considered

| Option | Freshness | Consistency | Verdict |
|--------|-----------|-------------|---------|
| **A. Per-plan pin + layered adoption policy** | New plans latest; in-flight adopt via policy | High (pinned by default) | **Chosen** |
| B. Always latest (global `DRAFT`) | Maximal | Poor — whiplash, non-reproducible in-flight | Rejected as default (fine for dev) |
| C. Always pinned; change only via redeploy/new plan | Low | Maximal | Too rigid — never applies improvements (incl. safety) to in-flight plans |
| D. Human-only adoption decisions | Manual | High | Safe but doesn't scale |

## Trade-off Analysis

Option A is the only one that satisfies both forces: in-flight plans stay **coherent and
reproducible** (pinned by default), while **safety updates still reach them immediately** (the
deterministic floor) and genuine improvements can be adopted with judgment (governance sub-agent)
and consent (HITL). It mirrors how expert humans handle mid-engagement knowledge updates. The cost
is added machinery — version stamping in plan state, a governance sub-agent, and change metadata on
publish — but each piece reuses existing capabilities (ADR-0002 state, ADR-0001 factory + interventions,
ADR-0006 fetch).

### Governance sub-agent — guardrails

- **The deterministic floor sits *above* it.** Safety-critical recalls force-apply without consulting the sub-agent; a non-deterministic agent must never veto a safety override.
- **Pin the governor itself.** The change-governance agent runs on a **vetted, pinned** prompt — it must not be mid-change while deciding on changes ("who governs the governor").
- **Verify the consequential ones.** High-impact adoptions get a second critic sub-agent (adversarial lens) before applying; cheap/ambiguous ones are single-pass. Control cost by classifying once, invoking the sub-agent only for the ambiguous class, and batching/caching decisions across affected plans.

## Consequences

- **Easier:** coherent, reproducible long-running plans; safety updates still propagate immediately; improvements adopted with judgment + consent; full audit of which version drove each decision (also feeds the future data flywheel).
- **Harder:** more moving parts — per-plan version stamping, a governance sub-agent, and mandatory change metadata on publish; a non-deterministic adoption decision that must be bounded by the deterministic floor and (for high-stakes) verified.
- **To revisit:** batching/caching strategy at scale; how `adopt_at_milestone` checkpoints are defined; whether the governance sub-agent’s decisions should themselves be evaluated against outcomes over time.

## Scope: MVP vs. roadmap

- **MVP (now):** deterministic only — stamp the prompt version into the plan on creation; in-flight plans keep their pinned version; **safety-critical = manual force-apply**; new plans use the latest. Real-time `DRAFT` fetch (ADR-0006) applies to new/dev work. This is demonstrable and safe.
- **Roadmap:** the **governance sub-agent** (intelligent tier), HITL adoption prompts, adversarial verification, and automated safety-classification. Novel differentiator: an agent platform that reasons about whether to adopt its *own* updates mid-engagement.

## Action Items

1. [ ] Add prompt/skill **version stamp(s)** to the plan/task record on creation (extends the ADR-0002 schema).
2. [ ] Record the **version in force** on every tracked decision in the event log.
3. [ ] Define the **change-metadata** contract on version publish (`change_type` + rationale).
4. [ ] MVP: deterministic adoption (pin per plan; new plans latest; manual safety force-apply).
5. [ ] Roadmap: implement the **change-governance sub-agent** (structured output, pinned prompt), HITL adoption, and adversarial verification for high-stakes changes.
