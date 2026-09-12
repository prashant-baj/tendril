# ADR-0002: Context Management & Durable State

**Status:** Accepted
**Date:** 2026-08-19
**Deciders:** Project owner / lead engineer

## Context

Tendril's engagements are **long-running** (days to a full harvest season) and **multi-tenant** (many users, gardens, and plants). Two related pressures follow:

- **Context pressure (in-model).** A single interaction accumulates conversation turns plus large tool outputs — photos, vision/diagnosis results, weather payloads, knowledge-base passages. Left unmanaged this overflows the model's context window and inflates cost and latency.
- **Durability (across time & compute).** AgentCore runtime sessions are **ephemeral** — compute spins up on invoke and is reclaimed after idle/max-lifetime, and the outcome loop deliberately pauses for days between check-ins. So nothing can live only in process memory: the plan, the garden's history, and the conversation must survive across sessions and be retrievable weeks later.

These are two sides of one question — *what does the agent need in mind right now (bounded, cheap) versus what must persist and be queryable* — so we decide them together to keep the boundary explicit and avoid double-storing.

A further requirement: the outcome loop and its **tracker/scheduler** need **deterministic, structured queries** ("which plans have follow-ups due today?", "is the success criterion met?"), and the future **data flywheel** needs structured, captured events (see ADR-0001 and `project-context.md`). Semantic recall alone is not enough for these.

## Decision

Adopt a **three-tier model** that separates working context from durable conversational state from durable structured state:

1. **Working context (in-model)** — managed by **Strands Context Management**: `auto` mode (summarization + `ContextOffloader` for large tool results), with the garden vision and each goal's success criteria **pinned** so they survive compression; `SlidingWindowConversationManager` per agent where history is predictable.
2. **Durable conversational state (cross-session)** — **Strands Storage** (`S3Storage`) + Session Management + **Memory** (`MemoryManager` on **Bedrock Knowledge Bases**), with storage/memory **stores scoped per user and per garden** for multi-tenant isolation and semantic recall of garden history.
3. **Durable structured state (application source of truth)** — an **external DynamoDB** store owned by the application, holding the domain model — Garden, Plant, Goal, Plan, Task, Tracking — plus success criteria, progress, and a capture-first **event log**. This drives the tracker/scheduler and seeds the future data flywheel.

Offloaded/summarized content is persisted to **S3Storage** (not the default in-memory), because Strands' context managers offload to in-memory storage unless a persistent backend is configured.

## Options Considered

### Option A: Three-tier — Strands context management + Strands storage/memory + external DynamoDB (chosen)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium — two persistence systems with a defined boundary |
| Cost | Low-Medium — S3/Bedrock KB/DynamoDB, all serverless/on-demand |
| Scalability | High — all managed, multi-tenant scoping built in |
| Queryability | High — structured DynamoDB for the loop/scheduler/flywheel |
| Team familiarity | Medium — DynamoDB is well understood; Strands memory is newer |

**Pros:** Bounds context cost/latency; durable across ephemeral compute; **structured, queryable** state for the tracker, scheduler, and analytics; semantic recall of garden history via Memory; multi-tenant scoping; capture-first data by design.
**Cons:** Two persistence systems to keep coherent; requires an explicit ownership boundary (conversation/semantic vs. structured domain).

### Option B: Strands-only (rely on Memory + Storage for everything, no structured DB)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low — one system |
| Cost | Low |
| Scalability | High |
| Queryability | **Low** — semantic recall, not transactional structured queries |
| Team familiarity | Medium |

**Pros:** Simplest; least to build; native to Strands.
**Cons:** Bedrock Knowledge Bases excels at semantic recall of unstructured knowledge, but is **not** a transactional, queryable store for structured entities, due-date scheduling, progress state, or analytics. The outcome loop, tracker, and data flywheel would be brittle or impossible.

### Option C: Fully custom (bypass Strands context + memory; build our own)

| Dimension | Assessment |
|-----------|------------|
| Complexity | High |
| Cost | High (engineering) |
| Scalability | Depends on us |
| Queryability | High |
| Team familiarity | High |

**Pros:** Maximum control.
**Cons:** Re-implements context summarization/offloading and cross-session memory that Strands provides for free; unjustifiable on this timeline.

### Option D: In-memory only (no durable state)

Rejected outright: AgentCore sessions are ephemeral and the loop spans weeks — state would be lost between check-ins.

## Trade-off Analysis

The decisive question is **B vs. A**: is Strands Memory enough on its own? Memory (Bedrock Knowledge Bases) is excellent for *"what has happened with this garden"* — unstructured, semantic recall injected into prompts. But Tendril's outcome loop is a **stateful, scheduled process**: it must deterministically answer "which plans have a follow-up due now?", "has this success criterion been met?", and record structured outcomes for later aggregation. Those are transactional/queryable operations, not similarity search. So the two are **complementary, not competing** — Memory for semantic history, DynamoDB for structured domain state — and using both is cheaper and more robust than forcing either to do the other's job.

For working context, Option A's use of Strands Context Management is clearly correct; the only sub-choice is strategy. We choose **`auto` (summarize + offload) with pinning** for the MVP (protects vision and success criteria), reserving the experimental `agentic` mode for later and `SlidingWindow` for agents with predictable, short histories.

## Consequences

- **Easier:** Bounded, predictable context cost; durability across ephemeral compute; deterministic queries for the tracker/scheduler; semantic recall of history; multi-tenant isolation; capture-first data for the flywheel.
- **Harder:** Two persistence systems must stay coherent — a clear **ownership boundary** is required (conversation/semantic → Strands Storage+Memory; structured domain → DynamoDB), and memory-extraction must not silently diverge from structured state. Offloaded content **must** be configured to a persistent backend (S3), not left in the default in-memory store.
- **To revisit:** Whether Memory can absorb more of the durable state to shrink DynamoDB scope; adopting `agentic` context mode once it is stable; retention/TTL policies per tier.

## Data Ownership Boundary

| Tier | Mechanism | Contents | Lifetime |
|------|-----------|----------|----------|
| Working context | Strands Context Management (`auto` + pins, sliding window) | Active conversation, recent tool results, pinned vision & success criteria | Current invocation (bounded) |
| Conversational durability | Strands Storage (`S3Storage`) + Session Mgmt + Memory (Bedrock KB) | Session continuity, offloaded content, semantic garden history | Across sessions; scoped per user/garden |
| Structured domain state | External DynamoDB | Garden/Plant/Goal/Plan/Task/Tracking, success criteria, progress, event log | Source of truth; long-lived; drives loop/scheduler/flywheel |

## Action Items

1. [ ] Configure **`S3Storage`** as the Strands Storage backend (offloading, session management, memory) — explicitly, not the default in-memory store. **Design specified** in [`data-architecture.md`](../data-architecture.md) §3.1/§3.3 (`tendril-{env}-agent-state` bucket, `session_id = goal_id`); provisioning it is a follow-up (§9 there).
2. [ ] Set the conversation manager to **`auto`** with **pins** for garden vision and goal success criteria; apply `SlidingWindow` where history is predictable. Pinning mechanism refined to **`ContextInjector`**, not the `agentic` mode's model-invoked `pin_context` — see [`strands-capability-mapping.md`](../strands-capability-mapping.md).
3. [ ] Enable **Memory** (`MemoryManager`, Bedrock Knowledge Bases backend) with **stores scoped per user/garden**. **Design specified** in [`data-architecture.md`](../data-architecture.md) §3.2 — two Knowledge Bases (Garden Memory, scoped; Horticultural Reference, shared read-only), not one.
4. [x] Define the **DynamoDB schema** for Garden/Plant/Goal/Plan/Task/Tracking + a capture-first event log, and the query patterns the tracker/scheduler need (e.g., follow-ups due) — **done in [`data-architecture.md`](../data-architecture.md) §2**, including the `TasksDueIndex` GSI for the due-follow-ups query.
5. [ ] Document and enforce the **ownership boundary** (what lives in Strands vs. DynamoDB) to prevent divergence — the *data* ownership boundary is documented (`data-architecture.md` §§1-5); the **agent access boundary** (who may reach which store) is now its own decision: [ADR-0013](./0013-agent-data-access-boundary.md).
6. [ ] Apply **privacy-by-design** to all durable tiers — consent, PII minimization, per-tenant isolation, retention/TTL (see `../../engineering-best-practices.md`). Tenant isolation is now concrete (`data-architecture.md` §8); retention/TTL policy per entity is still open.
