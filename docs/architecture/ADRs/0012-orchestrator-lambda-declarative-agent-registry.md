# ADR-0012: Orchestrator as an async Lambda, with a declarative agent registry

**Status:** Accepted
**Date:** 2026-09-11
**Deciders:** Project owner / lead engineer

## Context

ADR-0001 adopted Strands for model-driven multi-agent orchestration but explicitly **deferred**
one part of the design (action item 5): *"Validate agents-as-tools vs. in-process Swarm/Graph
for the orchestrator↔specialist topology (candidate for its own ADR)."* This ADR resolves that.

Two more things motivate resolving it now:

- `infra/stacks/agentcore_stack.py` currently provisions exactly one hardcoded agent
  (`hello_agent`) via a single Python call to `_agent_runtime(...)`. That doesn't scale to "a
  team of specialist agents" (project-context.md §7) — adding a specialist today means editing
  Python in a shared stack file, not a declarative, reviewable, per-agent change.
- ADR-0004 already committed the backend to an **async, event-driven** shape (Client API Lambda
  persists fast and returns; EventBridge routes async work; results push over the WebSocket
  channel) specifically because agent work is long-running and can't complete inside a
  synchronous HTTP request. An orchestrator therefore belongs on the async side of that design,
  not inline in the Client API's request/response cycle.

## Decision

**The orchestrator runs as a Lambda function** (`app/orchestrator/`), triggered asynchronously —
not inline in the Client API request path. **Specialist agents stay on Bedrock AgentCore**
(ADR-0001 unchanged for specialists). The orchestrator uses the Strands SDK's **agents-as-tools**
pattern: each specialist is exposed to the orchestrator's model loop as a tool whose
implementation calls AgentCore's `InvokeAgentRuntime` against that specialist's deployed runtime.
Which specialists exist is **declarative and deploy-time**, driven by a single registry that both
provisioning (CDK) and invocation (the orchestrator Lambda) read from.

### Trigger flow (per ADR-0004's async model)

```
Client API Lambda → persist to DynamoDB → EventBridge event → Orchestrator Lambda (async)
                                                                      ↓ (Strands agent loop)
                                                     specialist tool calls → AgentCore InvokeAgentRuntime
                                                                      ↓
                                                     result → DynamoDB + WebSocket push
```

### Declarative agent registry

- `agents/registry/*.json` — one file per specialist agent (mirrors the existing
  `guardrails/*.json` / `prompts/*.md` policy-as-data convention, ADR-0006/ADR-0008). Each entry
  declares: `name`, `folder` (its `agents/` subfolder — Dockerfile + code), `model_id`,
  `prompt_name`, `guardrail_name`, `description` (also the tool description the orchestrator's
  model sees — *this is how the model knows when to reach for this specialist*), and any
  `tools` the specialist itself needs (Tool APIs).
- `AgentCoreStack` is refactored to **loop over the registry directory** and provision one
  `aws_bedrockagentcore.Runtime` per entry (replacing the current single hardcoded call) —
  adding/removing a specialist becomes "add/remove a registry file," matching "declarative
  control on what agents are deployed."
- The orchestrator Lambda's invocation manifest (agent name → runtime ARN + description) is
  built from the **same registry** at synth time and passed to the orchestrator via environment
  variables (or a Parameter Store object if it outgrows env var limits) — **not** discovered at
  runtime via `ListAgentRuntimes`. The set of specialists available to a given deploy is fixed
  and reviewable in a diff; only *which* of them the model chooses to call, and in what order,
  is decided at runtime (preserving ADR-0001's core "no fixed execution order" requirement at
  the level it actually applies — sequencing, not existence).

## Options Considered

### Orchestrator↔specialist topology

| Option | Isolation / independent deploy | Matches "declarative registration" | Verdict |
|--------|-------------------------------|--------------------------------------|---------|
| **A. Agents-as-tools over remote AgentCore `InvokeAgentRuntime`, registry-driven (chosen)** | Yes — each specialist keeps its own runtime, model, guardrail, independent deploy | Yes | **Chosen** |
| B. In-process Swarm/Graph (specialists run as Python objects inside the orchestrator) | No — one shared process/package for every specialist's dependencies, model, config | N/A | Rejected — specialists lose independent deploy/guardrail/model config; bloats the orchestrator's Lambda package |
| C. Runtime discovery via `ListAgentRuntimes` (tag-based), no registry file | Yes | No — explicitly not declarative; what's "in" orchestration silently tracks whatever happens to be deployed | Rejected per explicit ask |
| D. Orchestrator also on AgentCore (not Lambda) | Yes | Orthogonal | Rejected per explicit ask; noted below as a revisit trigger |

### Orchestrator compute target

| Option | Fits ADR-0004's async model | Ceiling | Verdict |
|--------|------------------------------|---------|---------|
| **A. Lambda, EventBridge-triggered (chosen)** | Yes — same shape as ingestion/tracker | 15 min max duration | **Chosen** |
| B. AgentCore Runtime | Yes, but adds a second "how do I invoke this asynchronously" pattern alongside Lambda's | No practical ceiling for a single session | Rejected for now — see "to revisit" |

## Trade-off Analysis

The decisive fact is that the **long-running part of "long-running agent work" already lives in
DynamoDB + EventBridge wake-ups** (ADR-0004's tracker/outcome loop across days-to-weeks), not in
a single live orchestrator invocation. One orchestration **turn** — decompose a goal, call a
handful of specialists, reconcile, propose a plan — is expected to complete in seconds to low
minutes, comfortably inside Lambda's 15-minute ceiling, and Lambda's scale-to-zero between turns
is a real cost saving AgentCore's persistent-session model doesn't give for free. Registry-driven
agents-as-tools (A) costs a small amount of duplication — the registry is read once by
`AgentCoreStack` for provisioning and once by the orchestrator stack for its invocation manifest
— but that duplication *is* the declarative, reviewable control being asked for, and it directly
reuses the policy-as-data pattern already accepted for prompts and guardrails rather than
inventing a new one.

## Consequences

- **Easier:** adding a specialist = a new `agents/registry/*.json` file + its agent folder, no
  orchestrator code change; the orchestrator scales to zero between turns; each specialist keeps
  independent model/guardrail/deploy configuration; the set of deployed specialists is visible in
  a single directory listing and reviewable in a PR diff.
- **Harder:** the orchestrator Lambda needs `bedrock-agentcore:InvokeAgentRuntime` IAM
  permissions scoped to registered runtimes; each specialist's AgentCore cold start now sits
  inside the orchestrator's own Lambda timeout budget, and sequential specialist calls stack
  latency; the registry file format is a contract two stacks depend on and needs its own schema
  discipline (candidate for the same JSON-Schema/CI-lint tooling as ADR-0011's OpenAPI spec).
- **To revisit:** parallelizing specialist tool calls to bound total turn latency; moving the
  orchestrator to AgentCore if turn durations start approaching Lambda's 15-minute ceiling in
  practice; whether the registry needs formal JSON-Schema validation wired into `ci.yml`
  (mirroring the guardrail/prompt policy-as-data validation already in place).

## Action Items

1. [ ] Define the registry JSON shape and migrate `hello_agent` into `agents/registry/hello.json`.
2. [ ] Refactor `AgentCoreStack` to loop over `agents/registry/*.json` instead of the single
   hardcoded `_agent_runtime()` call.
3. [ ] Scaffold `app/orchestrator/` (Strands agent loop; agents-as-tools built from the registry
   manifest; `InvokeAgentRuntime` tool implementation).
4. [ ] Grant the orchestrator's execution role scoped `bedrock-agentcore:InvokeAgentRuntime` on
   registered specialist runtime ARNs only.
5. [ ] Wire the EventBridge trigger from the (future) Client API/ingestion Lambda to the
   orchestrator per the async flow above.
6. [ ] Mark ADR-0001's action item 5 resolved, pointing here.
