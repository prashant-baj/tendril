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
- [x] `PromptsStack` loops over a catalog keyed by logical prompt name (`prompts/<name>.md`)
  instead of the single hardcoded hello prompt, provisioning a `CfnPrompt` + version per entry.
- [x] `GuardrailsStack` loops over `guardrails/*.json` instead of the single hardcoded hello
  guardrail, provisioning a `CfnGuardrail` + version per file.
- [x] `AgentCoreStack` loops over `agents/registry/*.json` (per ADR-0012/WS-02) and provisions
  one `aws_bedrockagentcore.Runtime` per entry, **all built from the same `template` image** —
  never one Docker build per agent (ADR-0012's 2026-09-12 refinement). Verified directly in a
  synthesized template: `HelloRuntime` and the second specialist's runtime reference the
  *identical* `ContainerUri` (same CDK asset hash) — CDK deduplicated the build since both
  registry entries point at the same `hello_agent/` source directory.
- [x] A second specialist deploys successfully alongside `hello` with **zero changes to any
  stack's Python code**. **Deviation from the original plan:** the proof specialist built here
  is **vision** (`agents/registry/vision.json`, a plant-photo-inspection agent on
  `google.gemma-3-27b-it`), not the originally-planned **Agronomy** — a real, immediately-useful
  need (2026-09-12) came up before Agronomy did. The generalization work is identical either
  way; Agronomy is still exactly as easy to add now (one registry file + one prompt + one
  guardrail file) as this ADR/story always intended — it just isn't the one that happened to
  prove it.
- [x] Each specialist's execution IAM only grants the tools it actually declares in its own
  registry entry — no specialist can invoke a tool it didn't ask for. **Closed by AF-03
  (2026-09-13):** each specialist now gets its own execution role (`_make_agent_role`, replacing
  the single shared `AgentExecRole` this AC originally described); `lambda:InvokeFunctionUrl` is
  granted per-role, only for that specialist's own declared tools.
- [x] `cdk synth` succeeds for `dev`, env-prefixed, no hardcoded account IDs (verified with a
  real Docker build). `cdk deploy` not yet re-run after this change.
- [x] CDK assertion tests: registry/prompt/guardrail file count → provisioned resource count
  (`test_one_runtime_provisioned_per_registry_file` adds a fixture file and re-asserts the
  count, proving the loop); per-agent tool IAM scoping is now covered by AF-03's
  `test_tool_iam_scoped_to_the_specialist_that_declares_it`.

**Tasks**
- [x] Refactor `PromptsStack`/`GuardrailsStack` to loop over their data folders.
- [x] Confirm `AgentCoreStack`'s registry loop (from WS-02) builds one shared `template` image,
  not per-entry images.
- [x] Author `agents/registry/vision.json` + real `prompts/vision-system.md` +
  `guardrails/vision_guardrail.json` (not placeholders — AF-04's "author the second
  specialist's real guardrail policy" task is done here too, for `vision` instead of Agronomy).
- [x] Add/extend CDK assertion tests for the N-item loops and per-agent tool IAM scoping (AF-03).

**2026-09-12 update:** `hello` — the stand-in specialist used above to prove the N-item loop —
has since been decommissioned as a live registry entry. `agents/registry/hello.json`,
`guardrails/hello_guardrail.json`, and `prompts/hello-system.md` are deleted; the orchestrator no
longer sees or can call a `hello` specialist. `agents/hello_agent/` (the folder) stays exactly
where it is — it's the shared template `vision.json`'s `template` field still points at, per
AF-01's proof above. Only its role changed: from "also a deployed proof specialist" to "template
code only." CDK assertion tests were updated accordingly (guardrail/prompt resource counts are
now 1, not 2).

**Dependencies:** WS-02 (registry-loop foundation), ADR-0006, ADR-0008, ADR-0012.
**Status:** ✅ done (proven with `vision`, not the originally-planned Agronomy) — the one AC that
was deferred (per-agent tool IAM scoping) closed when AF-03 gave it a real tool to scope against.

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
- [x] A uniform **Tool API** convention: each tool is a Lambda + **Function URL** endpoint
  (`AuthType: AWS_IAM`), defined contract-first (`app/tools/<name>/openapi.yaml`, same shape as
  ADR-0011 scaled to one operation). **Deviation from the AC's literal wording:** Function URL,
  not API Gateway — this tool is internal/server-to-server only (no public frontend caller), so a
  full `SpecRestApi` + OpenAPI-import stack is unwarranted machinery for one operation; the
  OpenAPI file is still the contract, just not a CDK deploy artifact. `infra/stacks/agentcore_stack.py`
  provisions one Lambda+URL per `app/tools/<name>/` folder found on disk — directory-driven like
  the registry/prompts/guardrails, not a stack-code change per tool.
- [x] **Weather** is the reference implementation: one Lambda, one operation
  (`GET ?lat=&lon=`), returning a small typed forecast from Open-Meteo (real, not a stub).
- [x] The template agent (`agents/hello_agent/agent.py`) turns `TOOLS`/`TOOL_ENDPOINTS` (env vars
  injected per-registry-entry) into bound Strands tools — `build_tools()`/`make_tool()`/
  `call_tool_endpoint()` are 100% tool-agnostic; a second tool needs a new `app/tools/<name>/`
  folder + a registry `tools` entry, never new binding code. Calls are SigV4-signed with the
  runtime's own execution-role credentials (service=`lambda`) since every tool requires
  `AuthType: AWS_IAM`.
- [x] Each specialist's execution role grants invoke access **only** to the tools it declares —
  **this AC also closes AF-01's deferred one**: every specialist now gets its *own* execution
  role (`_make_agent_role`, replacing the single shared `AgentExecRole`), so
  `lambda:InvokeFunctionUrl` is granted per-specialist, not repo-wide. Verified in
  `infra/tests/test_stacks.py::test_tool_iam_scoped_to_the_specialist_that_declares_it` (a
  fixture specialist with `tools: []` does **not** get the grant) and directly in a real
  `cdk synth`: the statement's `Resource` is `WeatherTool`'s own ARN via `Fn::GetAtt`, attached
  only to `VisionExecRoleDefaultPolicy`.
- [x] Unit tests: `app/tools/weather/tests/test_handler.py` (valid/invalid/missing/out-of-range
  coordinates, upstream failure) and `agents/hello_agent/tests/test_agent.py`'s tool-binding
  tests (`build_tools`/`make_tool`/`call_tool_endpoint`, HTTP + SigV4 mocked, no live network).
- [ ] One manual smoke test recorded: an agent with `tools: ["weather"]` in `dev` calls Weather
  and gets a real forecast back. **Not yet run** — needs a `cdk deploy` first; `vision.json` now
  declares `tools: ["weather"]` and `vision-system.md` was updated so the model knows when to use
  it (only when a location is already known — it's told not to guess/ask for coordinates).

**Tasks**
- [x] Author the Tool API OpenAPI contract + the Weather Lambda + its Function URL infra.
- [x] Implement the template agent's generic HTTP-tool binding (registry tool name → endpoint +
  SigV4 auth → Strands tool).
- [x] Unit tests for the Weather handler and the binding mechanism (mocked HTTP).
- [x] Document the pattern (`app/tools/weather/README.md`) so a second tool is "copy this," not
  "invent a new mechanism."

**Dependencies:** AF-02, ADR-0011 (contract-first pattern reused at tool scale). **Note:** built
directly on AF-01 without AF-02's formal rename (`hello_agent/` → `template_agent/`) — that
rename was explicitly skipped (2026-09-13) since it adds no capability; the tool-binding code
lives in `agents/hello_agent/agent.py` today, same file AF-02 would still relocate.
**Status:** ◐ done in substance — only the manual dev smoke test remains, pending deploy.

---

## AF-04 — Guardrails: author the Agronomy specialist's real guardrail policy

> **Note (2026-09-12):** `guardrails/vision_guardrail.json` was authored alongside AF-01, for
> the **vision** specialist rather than Agronomy — it proves "a second real (non-placeholder)
> guardrail deploys through AF-01's generalized loop," but reuses `hello`'s topic definitions
> verbatim rather than the domain-specific tuning + eval-scenario work this story actually
> calls for below. **Still genuinely open**, just for whichever specialist picks it up next
> (Agronomy or a vision-specific tuning pass) — this story's AC/Tasks are otherwise unchanged.

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
