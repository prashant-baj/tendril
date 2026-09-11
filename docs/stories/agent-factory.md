# Stories — Epic: Agent Factory (template, tools, guardrails, memory & context)

Makes the "agent factory" principle from
[ADR-0001](../architecture/ADRs/0001-use-strands-agents-framework.md) real: **one shared agent
codebase, specialized purely by configuration.** `agents/hello_agent/` today is a one-off; this
epic generalizes it into a template every specialist reuses, adds a second real specialist
(**Agronomy**) as proof, gives agents Lambda-backed Tool APIs to call, and wires the
per-user/per-garden memory and context management ADR-0001 already committed to (action items 6
and 8) but never implemented.

Each story is scoped to **one layer**, matching how you described them: infra, the template
agent itself, tools-as-Lambda, guardrails, and memory/context. All build-ready: explicit
Acceptance Criteria, conforming to [ADR-0001](../architecture/ADRs/0001-use-strands-agents-framework.md),
[ADR-0006](../architecture/ADRs/0006-prompt-externalization-bedrock-prompt-management.md),
[ADR-0008](../architecture/ADRs/0008-guardrails-layered-defense-in-depth.md), and
[ADR-0012](../architecture/ADRs/0012-orchestrator-lambda-declarative-agent-registry.md)
(including its 2026-09-12 refinement note — **an agent is its config, not its code**).

**Story format:** `As a <role>, I want <capability>, so that <benefit>` + Acceptance Criteria +
Tasks + Dependencies + Status.

**Scope boundary:** this epic proves the factory scales past one agent using **Agronomy** as the
second specialist and **Weather** as the reference tool. It does **not** author the remaining
seven specialist agents or the other four tools from `project-context.md` §7 — those become
near-copy follow-up stories once AF-01..AF-05 land (see "Carried forward").

**Suggested order:** AF-01 → AF-02 → AF-03 → AF-04 → AF-05 (AF-03/AF-04 can run in parallel with
each other once AF-01/AF-02 exist).

**Status legend:** ✅ done · ◐ partially done · ☐ to do.

---

## AF-01 — Infra: generalize Prompts/Guardrails/AgentCore to N specialists

**As a** developer, **I want** `PromptsStack`, `GuardrailsStack`, and `AgentCoreStack` all
generalized from "one hardcoded hello item" to "loop over N data files," proven by deploying a
second real specialist (**Agronomy**) with zero stack-code changes, **so that** adding a
specialist is only ever a data change (ADR-0001, ADR-0012).

**Acceptance Criteria**
- [ ] `PromptsStack` loops over `prompts/*.md` (one file per specialist) instead of the single
  hardcoded hello prompt, provisioning a `CfnPrompt` + version per file.
- [ ] `GuardrailsStack` loops over `guardrails/*.json` instead of the single hardcoded hello
  guardrail, provisioning a `CfnGuardrail` + version per file.
- [ ] `AgentCoreStack` loops over `agents/registry/*.json` (per ADR-0012/WS-02) and provisions
  one `aws_bedrockagentcore.Runtime` per entry, **all built from the same `template` image** —
  never one Docker build per agent (ADR-0012's 2026-09-12 refinement).
- [ ] `agents/registry/agronomy.json` (referencing a real `tendril-<env>-agronomy-system` prompt
  and `tendril-<env>-agronomy-guardrail` guardrail) deploys successfully alongside `hello` with
  **zero changes to any stack's Python code**.
- [ ] Each specialist's execution IAM only grants the tools it actually declares in its own
  registry entry — no specialist can invoke a tool it didn't ask for.
- [ ] `cdk synth`/`cdk deploy` succeed for `dev`, env-prefixed, no hardcoded account IDs.
- [ ] CDK assertion tests: N registry/prompt/guardrail files → N provisioned resources; adding a
  fixture file changes the count without touching stack code; per-agent tool IAM is scoped, not
  global.

**Tasks**
- [ ] Refactor `PromptsStack`/`GuardrailsStack` to loop over their data folders.
- [ ] Confirm `AgentCoreStack`'s registry loop (from WS-02) builds one shared `template` image,
  not per-entry images.
- [ ] Author `agents/registry/agronomy.json` + a placeholder `prompts/agronomy-system.md` +
  `guardrails/agronomy_guardrail.json` stub (real content is AF-04's job — a minimal valid file
  is enough here to prove the loop).
- [ ] Add/extend CDK assertion tests for the N-item loops and per-agent tool IAM scoping.

**Dependencies:** WS-02 (registry-loop foundation), ADR-0006, ADR-0008, ADR-0012.
**Status:** ☐ to do.

---

## AF-02 — Template agent: one config-driven codebase for every specialist

**As a** developer, **I want** the actual agent code — entrypoint, model/prompt/guardrail
resolution, tool binding, memory/context hooks — to be a single shared template every specialist
runs unmodified, **so that** "adding a specialist" never means writing new Python.

**Acceptance Criteria**
- [ ] `agents/hello_agent/`'s code is generalized into the shared template location
  (`agents/template_agent/`) used by every registry entry via `template` (ADR-0012's refinement)
  — one codebase, one Dockerfile.
- [ ] The template resolves `PROMPT_NAME`, `GUARDRAIL_NAME`, `MODEL_ID` exactly as `hello_agent`
  already does (PG-01/PG-04's resolve → fetch/attach → fallback pattern) — no regression.
- [ ] The template reads a `TOOLS` env var (tool names from its registry entry) and binds each to
  a Strands tool via AF-03's mechanism; an agent with `tools: []` behaves exactly like
  `hello_agent` does today (no tools bound).
- [ ] Both `hello.json` and `agronomy.json` (AF-01) point at this same template; a real
  `prompts/agronomy-system.md` is authored (actual agronomy scope + tone), proving the *only*
  difference between the two deployed agents is configuration.
- [ ] Existing `hello_agent` unit tests (payload validation, prompt/guardrail resolve+fallback)
  pass unmodified against the generalized template.
- [ ] No agent-name branching (`if name == "agronomy"`) anywhere in the template — config only.

**Tasks**
- [ ] Relocate/generalize `agents/hello_agent/` code to the shared template location; update
  `Dockerfile`/`requirements.txt` references in CDK.
- [ ] Add `TOOLS` env parsing + the tool-binding hook (calling into AF-03).
- [ ] Author `prompts/agronomy-system.md`.
- [ ] Port existing hello-agent unit tests to the generalized template; add a test proving two
  different registry configs produce two differently-behaving agents from one codebase.

**Dependencies:** AF-01, TF-04. **Status:** ☐ to do.

---

## AF-03 — Tools: Lambda-backed Tool APIs, invoked by agents via a uniform contract

**As a** developer, **I want** each Tool implemented as its own Lambda behind an API (Weather
first), and a template-agent mechanism that turns a registry `tools` declaration into a real,
callable Strands tool, **so that** tool capabilities are genuinely swappable, independently
deployable APIs — never code baked into one agent (architecture.md §4.2, "tools are APIs").

**Acceptance Criteria**
- [ ] A uniform **Tool API** convention: each tool is a Lambda + API Gateway (or Function URL)
  endpoint, defined contract-first (same OpenAPI approach as ADR-0011, scaled down to one
  operation per tool).
- [ ] **Weather** is the reference implementation: one Lambda, one operation
  (`GET /tools/weather?lat=&lon=`), returning a small typed forecast payload.
- [ ] The template agent (AF-02) turns `tools: ["weather"]` into a bound Strands tool that calls
  the Weather API over HTTP — the binding mechanism itself is **tool-agnostic** (a second tool
  needs a new Lambda+API, not new binding code).
- [ ] Each specialist's execution role grants invoke access **only** to the tools it declares
  (verified in AF-01's IAM tests).
- [ ] Unit tests: the Weather Lambda handler (valid/invalid coordinates) and the template's
  tool-binding logic with a **mocked** HTTP call (no live network access in CI).
- [ ] One manual smoke test recorded: an agent with `tools: ["weather"]` in `dev` calls Weather
  and gets a real forecast back.

**Tasks**
- [ ] Author the Tool API OpenAPI contract + the Weather Lambda + its API Gateway/Function URL
  infra.
- [ ] Implement the template agent's generic HTTP-tool binding (registry tool name → endpoint +
  auth → Strands tool).
- [ ] Unit tests for the Weather handler and the binding mechanism (mocked HTTP).
- [ ] Document the pattern so a second tool is "copy this," not "invent a new mechanism."

**Dependencies:** AF-02, ADR-0011 (contract-first pattern reused at tool scale).
**Status:** ☐ to do.

---

## AF-04 — Guardrails: author the Agronomy specialist's real guardrail policy

**As a** safety/domain reviewer, **I want** `guardrails/agronomy_guardrail.json` authored with
real agronomy-specific policy — not AF-01's placeholder stub — **so that** the second real
specialist has the same layered safety floor as `hello` (ADR-0008), proving guardrail-per-agent
is reviewed policy, not just infra plumbing.

**Acceptance Criteria**
- [ ] Content filters match the existing baseline (HATE/INSULTS/SEXUAL/VIOLENCE/MISCONDUCT
  HIGH/HIGH + PROMPT_ATTACK on input).
- [ ] Denied topics are tuned to agronomy specifically — unsafe/off-label fertilizer or
  soil-amendment advice, and anything outside soil/nutrition/planting-medium scope — reworded
  for this domain, not copy-pasted from `hello`'s topics.
- [ ] PII anonymized: same EMAIL/PHONE/NAME/ADDRESS policy as `hello`.
- [ ] Safe, label-compliant agronomy guidance is **not** over-blocked — proven by an eval
  scenario (mirrors PG-07's over-block regression test).
- [ ] Deploys through AF-01's generalized `GuardrailsStack` loop — only a JSON file changes.
- [ ] `cdk synth` clean; no account-specific content in the JSON.

**Tasks**
- [ ] Author `guardrails/agronomy_guardrail.json` (replacing AF-01's stub) with real denied-topic
  definitions + examples.
- [ ] Extend `scripts/eval_guardrail.py` (or an agronomy-scoped variant) with on-domain,
  off-domain, and unsafe-vs-safe scenarios.
- [ ] Run the eval against `dev`; record actual vs. expected; tune for over-blocking.

**Dependencies:** AF-01. **Status:** ☐ to do.

---

## AF-05 — Memory & Context: per-user/per-garden memory for every specialist

**As a** developer, **I want** the shared template agent to wire Strands Memory (Bedrock
Knowledge Bases backend) and context management (summarization + offloading), scoped per
user/garden and configurable per specialist, **so that** every agent remembers relevant history
and stays within its context window over Tendril's long-running engagement (ADR-0001 action
items 6 & 8, Appendix A).

**Acceptance Criteria**
- [ ] The template initializes a Strands `MemoryManager` (Bedrock KB backend) when a registry
  entry's `memory.enabled` is `true`; store keys are scoped per **user + garden** — a test with
  two distinct user/garden scopes proves no cross-tenant recall.
- [ ] A `SummarizingConversationManager` (+ `ContextOffloader` for large tool results) bounds
  context growth; the garden's vision and current success criteria are **pinned** — never
  summarized away (ADR-0001 Appendix A).
- [ ] Memory/context settings are per-agent config (the `memory` block already reflected in
  `agents/registry/README.md`) — an agent with `memory.enabled: false` runs with no memory
  overhead, proving this is opt-in, not hardwired.
- [ ] IAM grants only the Bedrock Knowledge Bases actions actually needed, scoped to this
  account/region — no wildcard.
- [ ] Unit tests: memory scoping (two tenants never see each other's recall), context pinning
  (vision/success-criteria survive summarization), and the opt-out path.
- [ ] One manual smoke test recorded: a specialist recalls a prior turn correctly for the same
  user/garden, and recalls nothing for a different one.

**Tasks**
- [ ] Wire `MemoryManager` (Bedrock KB backend) into the template, scoped by user/garden key.
- [ ] Wire `SummarizingConversationManager` + `ContextOffloader`, with vision/success-criteria
  pinning.
- [ ] Thread the `memory` registry block through `AgentCoreStack`'s env-var injection.
- [ ] IAM scoping for Bedrock Knowledge Bases actions.
- [ ] Unit tests for scoping, pinning, and opt-out; document the manual smoke test.

**Dependencies:** AF-02, ADR-0001 Appendix A, ADR-0002 (context & durable state).
**Status:** ☐ to do.

---

### Definition of Done (applies to every story)

Per [`../engineering-best-practices.md`](../engineering-best-practices.md): CI green (lint +
tests + secret scan), **no hardcoded secrets/account identifiers**, deploys cleanly to **dev**
via the pipeline, touches **only the folders it needs**, updates relevant **docs/ADRs**, and is
**human-reviewed**.

### Carried forward (not in this epic)

The remaining seven specialist agents (pest, disease, irrigation, fertilizer, pruning,
weather-impact, beautification/landscaping — `project-context.md` §7) and the remaining four
tools (Plant-ID/Vision, Nursery/Market, Notification, Knowledge search) — each is a near-copy of
AF-01/AF-04's pattern (a registry entry + prompt + guardrail) or AF-03's pattern (a new Lambda +
API), not respecified here. Also carried forward: Cedar policy-based authorization and the wider
Interventions/HITL handlers (`deny`/`guide`/`confirm`/`transform`) beyond the base template
(ADR-0001 Appendix A); per-region guardrail tuning.
