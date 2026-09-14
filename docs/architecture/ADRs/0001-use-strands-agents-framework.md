# ADR-0001: Use AWS Strands Agents SDK as the multi-agent framework

**Status:** Accepted
**Date:** 2026-08-19
**Deciders:** Project owner / lead engineer

## Context

Tendril is a model-driven, multi-agent system: a user states a goal in plain language and an orchestrator must decide, **at runtime**, which specialist agents and tools to invoke and in what order. The core requirement is that the **combination and sequencing of agents and tools cannot be predefined** — it depends on the user's goal, the garden's context, and what incoming photos/data reveal.

Forces at play:

- **Model-driven orchestration** — the workflow must be *composed* by the model at runtime, not selected from a hardcoded graph.
- **Native multi-agent patterns** — we need agents-as-tools and swarm/graph composition of specialist agents (agronomy, pest, disease, irrigation, pruning, weather, beautification, landscaping).
- **Per-agent model flexibility** — different agents should be able to use different (including specialized/custom) models.
- **First-class AWS integration** — the project is all-in on AWS: Amazon Bedrock for models, Bedrock Guardrails, Bedrock Prompt Management, and AgentCore for runtime, sessions, memory, and observability, deployed via CDK.
- **Developer velocity** — a small team on a tight timeline; minimal boilerplate matters.
- **Tools as APIs** — capabilities should sit behind uniform, discoverable contracts (MCP / API tools).
- **Long-running, stateful outcomes** — tasks span days to a full season; durable state is required.

## Decision

Adopt the **AWS Strands Agents SDK** as Tendril's agent framework. Agents are model-driven (the LLM decides which tools/agents to call and when), specialized via externalized prompts/skills, and hosted on Amazon Bedrock AgentCore. Durable, long-horizon state is externalized to **DynamoDB / AgentCore Memory** rather than relying on any in-framework checkpointing.

## Options Considered

### Option A: AWS Strands Agents SDK (chosen)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low — tools are plain Python functions; ~20 lines for a working agent |
| Cost | Low incremental — runs on Bedrock/AgentCore we already use |
| Scalability | High — serverless AgentCore runtime, per-session isolation |
| AWS integration | Native — Bedrock default, AgentCore, built-in OpenTelemetry |
| Team familiarity | Medium — newer SDK, but existing AgentCore/CDK scaffolding in hand |

**Pros:** Model-driven orchestration matches the core requirement; native multi-agent patterns (agents-as-tools, Swarm, Graph); model-agnostic (per-agent model choice); MCP/tools-as-APIs support; first-class AWS deploy (AgentCore/Lambda/Fargate) with built-in observability; minimal code accelerates the build; composes cleanly with the existing config-driven agent-factory + CDK scaffolding.
**Cons:** No native checkpointing / pause-resume / time-travel; newer and smaller ecosystem (fewer third-party examples, some API churn).

### Option B: LangChain / LangGraph

| Dimension | Assessment |
|-----------|------------|
| Complexity | High — explicit nodes/edges/state machine to author |
| Cost | Neutral — model-cost equivalent |
| Scalability | High — mature, widely deployed |
| AWS integration | Portable but not AWS-native; no first-class AgentCore deploy story |
| Team familiarity | Medium-High — large ecosystem and docs |

**Pros:** Mature, huge ecosystem; **native checkpointing, pause/resume, and time-travel** debugging; excellent for deterministic, known workflows.
**Cons:** Graph/state-machine model requires **predefining the paths** — directly conflicts with Tendril's runtime-composed requirement; more boilerplate; not AWS-native (no first-class AgentCore/Bedrock deployment or built-in AWS observability).

### Option C: CrewAI

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low-Medium — declarative roles/tasks (YAML) |
| Cost | Neutral |
| Scalability | Medium |
| AWS integration | Not AWS-native |
| Team familiarity | Medium |

**Pros:** Clean declarative multi-agent "crews"; low ceremony for role/task setups.
**Cons:** More prescriptive, role/task-oriented orchestration; weaker fit for fully model-driven, unbounded runtime composition; no first-class AWS/AgentCore deployment.

### Option D: Microsoft AutoGen

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium-High |
| Cost | Neutral |
| Scalability | Medium |
| AWS integration | Not AWS-native |
| Team familiarity | Low-Medium |

**Pros:** Strong conversational multi-agent research pedigree; flexible agent-to-agent messaging.
**Cons:** Heavier; orchestration expressed through conversation patterns; not production/AWS-deployment focused; less aligned with our config-driven factory + AgentCore approach.

### Option E: Managed Amazon Bedrock Agents

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low — managed, lower-code |
| Cost | Neutral — Bedrock-native |
| Scalability | High — fully managed |
| AWS integration | Native |
| Team familiarity | Medium |

**Pros:** Fully managed, AWS-native, low operational overhead.
**Cons:** More opinionated/constrained; less control over custom model-driven multi-agent orchestration and the factory pattern; harder to express arbitrary inter-agent negotiation and per-agent model choice the way our design needs.

### Option F: Raw Amazon Bedrock Converse API (build our own)

| Dimension | Assessment |
|-----------|------------|
| Complexity | High — build the agent loop, tool routing, multi-agent glue ourselves |
| Cost | Neutral (model) / High (engineering) |
| Scalability | Depends entirely on us |
| AWS integration | Native (direct) |
| Team familiarity | High (it's just API calls) |

**Pros:** Maximum control; no framework dependency.
**Cons:** We would re-implement the agent loop, tool registry, and multi-agent patterns Strands already provides — unjustifiable cost on this timeline.

## Trade-off Analysis

The decisive axis is **model-driven vs. graph-driven orchestration**. Tendril's defining requirement — that the agent/tool combination and sequencing be composed at runtime — is Strands' home turf and is precisely what a graph framework like LangGraph asks you *not* to do (you must enumerate nodes and edges up front). LangGraph's genuine advantage, native durable state (checkpointing/pause-resume), is real but **not decisive here**, because our long-running outcome loop already needs to externalize state to a datastore for multi-tenant, multi-week use — so we get durability from **DynamoDB / AgentCore Memory** regardless of framework, neutralizing LangGraph's edge.

The secondary axis is **AWS-native deployment and observability**. Because the project is committed to Bedrock + AgentCore + CDK, Strands' first-class integration (Bedrock default, AgentCore runtime/sessions/memory, built-in OpenTelemetry) converts directly into scoring, velocity, and reuse of existing scaffolding — advantages the non-AWS-native options (LangGraph, CrewAI, AutoGen) don't provide. Managed Bedrock Agents is AWS-native but too constrained for our custom orchestration and factory pattern; raw Bedrock is too much undifferentiated engineering.

## Consequences

- **Easier:** Runtime-composed multi-agent orchestration; per-agent model choice; AWS-native deployment with built-in tracing; fast iteration (tools as plain functions); direct reuse of the existing AgentCore/CDK agent-factory scaffolding.
- **Harder:** We own durable state (no in-framework checkpointing) — must implement it on DynamoDB/AgentCore Memory. Testing a non-deterministic, model-driven system requires eval scenarios + trace inspection rather than exact-output assertions.
- **To revisit:** If a future use case genuinely needs deterministic, auditable, replayable execution paths, reconsider a graph/state-machine layer for that component. Monitor Strands' ecosystem maturity and API stability over the build.

## Action Items

1. [ ] Pin the Strands Agents SDK version and record it in each component's `requirements.txt`.
2. [ ] Configure the Strands MCP docs server (Build with AI) for the coding assistant to offset ecosystem newness.
3. [ ] Implement durable outcome-loop state on DynamoDB / AgentCore Memory (tracked in a separate ADR).
4. [ ] Establish eval scenarios + OpenTelemetry tracing as the testing approach for model-driven behavior.
5. [x] Validate agents-as-tools vs. in-process Swarm/Graph for the orchestrator↔specialist topology (candidate for its own ADR) — **resolved by [ADR-0012](./0012-orchestrator-lambda-declarative-agent-registry.md)**: agents-as-tools over remote AgentCore `InvokeAgentRuntime`, driven by a declarative agent registry.
6. [ ] Adopt Strands **Memory** (Bedrock Knowledge Bases backend) + **S3Storage**, with stores scoped per user/garden for multi-tenant isolation.
7. [ ] Implement plan approval and confirm-before-action via the **HumanInTheLoop** intervention (interrupt/resume), with the `ask` callback targeting **the web UI first** (REST `/plans/{id}/approve` + WebSocket push, ADR-0004) — a WhatsApp callback is a later, additive channel, not the initial implementation.
8. [ ] Configure **context management** (auto summarization + ContextOffloader; pin garden vision & success criteria) to bound context over multi-week histories.
9. [ ] Apply production settings: explicit tool lists, explicit model params (temperature/max_tokens/top_p), **Bedrock Guardrails** on outputs, `stream_async` streaming, and CloudWatch metrics.

> **Reprioritization (2026-09-12):** WhatsApp remains in scope long-term (project-context.md's
> vision), but is **deprioritized to a future backlog item**. HITL (plan approval,
> confirm-before-action) must work via the **web UI first** — the frontend already has the
> approve-plan screen (`frontend/src/app/features/goal-detail/`); the `HumanInTheLoop` `ask`
> callback should call into the Client API / WebSocket channel ADR-0004 already decided, not a
> WhatsApp integration that doesn't exist yet. See `docs/backlog.md`'s "Carried forward" list for
> the deprioritized WhatsApp item.

---

## Appendix A: Strands Capability Review

The following native Strands capabilities were reviewed for fit. Several materially reduce custom build and reinforce this decision.

| Capability | What Strands provides | Usefulness in Tendril |
|-----------|-----------------------|-----------------------|
| **[Storage](https://strandsagents.com/docs/user-guide/concepts/storage/)** | Pluggable backends — `InMemoryStorage`, `LocalFileStorage`, `S3Storage`, and custom (async `read/write/delete/list`); plugins auto-scope keys; underpins session management, context offloading, and memory | Use **S3Storage** (or a custom DynamoDB backend) for durable, multi-instance persistence of sessions, offloaded content, and memory across the multi-week outcome loop |
| **[Context Management](https://strandsagents.com/docs/user-guide/concepts/context-management/)** | Conversation managers — `auto` (SummarizingConversationManager, ~0.3 summary ratio / 0.85 compression + ContextOffloader for large tool results) and experimental `agentic` (model self-`summarize`/`truncate`/`pin`); `SlidingWindowConversationManager` | Gardens accumulate long histories, photos, and large weather/vision tool outputs; summarization + offloading keep within the window while **pinning** the garden vision and success criteria |
| **[Memory](https://strandsagents.com/docs/user-guide/concepts/memory/overview/)** | `MemoryManager` long-term memory across sessions — recall (`search_memory`), auto-injection (~5 entries per user turn), auto-extraction (~every 5 turns), `add_memory`; backends incl. **Bedrock Knowledge Bases**; multiple stores enable multi-tenant scoping | Core to "remember everything about your garden across seasons"; **per-user/per-garden store scoping = multi-tenant isolation**; auto-extraction feeds the capture-first data goal |
| **[Interventions](https://strandsagents.com/docs/user-guide/concepts/agents/interventions/)** | Composable control layer with typed actions (`proceed`/`deny`/`guide`/`confirm`/`transform`) at 5 lifecycle points; `Deny` short-circuits; Cedar policy-based authorization | Backbone for **safety & control**: gate irreversible/risky actions, enforce domain guardrails, transform/guide output, authorize per-user via Cedar |
| **[Human-in-the-Loop](https://strandsagents.com/docs/user-guide/concepts/agents/interventions/human-in-the-loop/)** | `HumanInTheLoop` handler pauses before tool calls (`confirm`); allow-list fast path, trust mode, optional LLM risk classifier; modes: **interrupt/resume**, stdio, **custom callbacks (web/Slack/WhatsApp)** | Directly implements Tendril's **plan-approval** and **confirm-before-action** steps; interrupt/resume maps to the async pause→notify→resume loop; a custom callback = **approve via WhatsApp** |
| **[Steering](https://strandsagents.com/docs/user-guide/concepts/agents/interventions/steering/)** | Runtime correction — before-tool (`proceed`/`guide`/`confirm`) and after-model (`proceed`/`guide`); `SteeringHandler` (code) or `LLMSteeringHandler` (natural-language rules); `ToolLedgerProvider` tracks tool calls/outcomes | Keep agents on-domain without prompt bloat; **experts express constraints in prose** (reinforces the expert-configured-agents moat); detect retry loops / repeated tool failures and redirect (resilience) |

> **Reconciliation with the trade-off analysis:** the "no native checkpointing / pause-resume" con noted against LangGraph is **narrower than first stated**. Strands' **interrupt/resume** (Interventions + Human-in-the-Loop) provides pause-and-resume, and **Storage + Session Management + Memory** provide cross-session durability with S3 / Bedrock Knowledge Bases backends. Tendril still externalizes the *structured* outcome-loop state (plan, success criteria, progress) to **DynamoDB** for queryability and the future data flywheel — but Strands covers more of the conversational durability and human-gating than the raw comparison implied, further strengthening this decision.

> **Superseded by a docs-verified review (2026-09-12):** this Appendix was written by reasoning
> about Strands' likely capabilities before fetching the live docs. It held up well, but
> [`docs/architecture/strands-capability-mapping.md`](../strands-capability-mapping.md) is now
> the authoritative version — verified against the actual API (`SnapshotSessionManager` +
> interrupt/resume for cross-Lambda-invocation HITL, `ContextInjector` for deterministic vision
> pinning, memory `scope` for tenant isolation, `strands-evals` for PG-07-style testing, and
> more). Prefer that document; this table is kept for history.

## Appendix B: Non-Functional Requirements & Production Operations

Notes distilled from [Operating Agents in Production](https://strandsagents.com/docs/user-guide/deploy/operating-agents-in-production/), mapped to Tendril. These NFRs are enforced via [`../../engineering-best-practices.md`](../../engineering-best-practices.md) and will be detailed in `docs/architecture.md`.

| NFR | Strands guidance | Tendril application |
|-----|------------------|---------------------|
| **Performance / latency** | Stream with `stream_async()`; bound context with `SlidingWindowConversationManager` | Stream responses to the user's channel; bound per-agent context |
| **Scalability / concurrency** | Deployment targets: **AgentCore** (serverless, agent-purpose-built), Fargate/App Runner, **EKS** (high concurrency), **Lambda** (short-lived/batch) | AgentCore for interactive specialist agents; Lambda for ingestion + the scheduled tracker; EventBridge for follow-up scheduling |
| **Security** | Explicit tool lists (disable auto-loading); validate inputs; sanitize outputs via guardrails; least-privilege tool permissions | Enforce **Bedrock Guardrails**; explicit per-agent tool lists; least-privilege IAM (see best-practices checklist) |
| **Observability** | Track tool execution time & error rates, token consumption (cost), end-to-end latency, agent error rates → CloudWatch | Built-in **OpenTelemetry / X-Ray** + CloudWatch dashboards for cost and error monitoring |
| **Error handling / resilience** | Robust try/catch, error logging, fallbacks, retry strategies | Timeouts, retries, and circuit breakers on all API tools; `ToolLedger`-based steering as a retry-loop backstop |
| **Configuration** | Explicit model config (temperature, max_tokens, top_p); specific tool lists; conversation-manager customization | Expose these in the per-agent **factory config surface**; no defaults left implicit |
| **Resource management** | `SlidingWindowConversationManager` to prevent context-window overflow | Apply per agent, tuned to expected history length |
