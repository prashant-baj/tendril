# ADR-0013: Agent data-access boundary — specialists never get direct data-store IAM

**Status:** Accepted
**Date:** 2026-09-12
**Deciders:** Project owner / lead engineer

## Context

ADR-0002 decided *where* durable state lives (DynamoDB for structured domain state, S3 + Bedrock
Knowledge Bases for conversational durability/memory) but not *who gets to reach it and how*.
That question sharpens now that `agents/registry/*.json` (ADR-0012) is about to make adding a
specialist agent a routine, declarative act — every new specialist is a new IAM principal, and
specialists are **dynamically selected by the orchestrator's own model reasoning**, not by
code the team reviews line-by-line. `CLAUDE.md` already states a hard rule that bears directly
on this: *"Tools are APIs; agents come from the factory; prompts are externalized."* This ADR
makes that rule's data-access consequence explicit and binding, rather than leaving it to drift
per specialist.

## Decision

**Specialist agents (AgentCore, from the template) never receive direct IAM access to
`AppTable`, `ConnectionsTable`, or the media/agent-state S3 buckets.** All domain data a
specialist needs arrives as **input from the orchestrator's tool-call payload** (the orchestrator
already read what it needs from DynamoDB/S3 and passes only the relevant fields); anything a
specialist must actively fetch or send lives behind a **Tool API** (Lambda + API Gateway, AF-03),
scoped per-specialist by its own registry entry's `tools` list.

**The orchestrator Lambda is the one exception** — it is backend application code (the same
trust tier as the Client API and Ingestion Lambdas, ADR-0004), not a dynamically-selected
specialist, so it retains direct, IAM-scoped access to `AppTable`, `ConnectionsTable`, the media
bucket, and its own session-state bucket.

**Bedrock Knowledge Bases (Memory, ADR-0001/AF-05) are a narrow, explicit second exception** —
both the orchestrator and specialists may hold direct, resource-scoped IAM for
`bedrock:Retrieve`/`bedrock:IngestKnowledgeBaseDocuments` against a *specific* Knowledge Base
ARN. This is not "direct data access" in the sense this ADR restricts: a Knowledge Base is
already an API-fronted, per-resource-scoped managed service (not a raw table or bucket), calling
it directly is how Strands' `MemoryManager`/`BedrockKnowledgeBaseStore` is designed to be used,
and interposing a proxy Lambda in front of it would add latency for no additional access control
the KB's own IAM scoping doesn't already provide.

## Options Considered

| Option | Least privilege | Blast radius if a specialist misbehaves | Consistency with existing hard rule | Verdict |
|--------|------------------|-------------------------------------------|--------------------------------------|---------|
| **A. Specialists: input + Tool APIs only; orchestrator: direct IAM; memory: direct IAM (chosen)** | High — each specialist's IAM is exactly its registry-declared tool list | Small — a compromised/tricked specialist can only call the tools it was scoped to, never read/write arbitrary domain records | Direct application of `CLAUDE.md`'s "tools are APIs" rule | **Chosen** |
| B. Every specialist gets scoped direct IAM to the DynamoDB items/S3 prefixes it needs | Medium — still per-agent scoped, but now N specialists each carry a distinct data-plane IAM policy to review, and specialists can query/scan structured state directly | Larger — a successful prompt injection against a specialist (ADR-0008's threat model) could turn "call the weather tool" into "scan the whole table," if the policy is even slightly too broad | Contradicts "tools are APIs" — the data store itself becomes the API | Rejected |
| C. All agents (orchestrator included) go through Tool APIs, zero AWS SDK calls from `app/orchestrator/` | Highest in theory | Smallest in theory | Over-applies the rule — the orchestrator isn't a dynamically-selected agent, it's reviewed backend code | Rejected — needless indirection and latency for a component already in the trusted tier; matches no existing service in ADR-0004 (Client API/Ingestion/Tracker/Notifier all read/write DynamoDB directly today) |

## Trade-off Analysis

The decisive distinction is **who decides what runs**: the orchestrator's code path is written
and reviewed by the team, like every other Lambda in `app/`; a specialist's *invocation* is
decided at runtime by an LLM interpreting a user's (and photo's) input, which is exactly the
threat model ADR-0008 already built layered guardrails for ("prompt injection via photos, text,
tool output"). Giving every specialist direct DynamoDB/S3 IAM would mean a successful injection
against *any one* specialist could read or corrupt data far outside that specialist's declared
purpose. Routing all specialist data access through the orchestrator's input and through Tool
APIs means the *worst* a compromised specialist can do is call the tools its own registry entry
already declared — which is also what AF-01 already requires ("execution IAM only grants the
tools it actually declares"), so this ADR is naming and generalizing a boundary that was already
implicit in that story, not inventing new work. The Knowledge Base exception is narrow and
justified on its own terms (see Decision) rather than a general carve-out.

## Consequences

- **Easier:** one clear rule to apply to every future specialist ("does it need a tool, or does
  the orchestrator already have this?") instead of a per-agent IAM design conversation each time;
  smaller, auditable per-specialist IAM policies; a compromised specialist's blast radius is
  bounded by its own tool list, not the whole data estate.
- **Harder:** the orchestrator must proactively fetch and pass down everything a specialist might
  need (it can't rely on the specialist fetching its own context), which means slightly more
  orchestrator-side data-shaping code; large payloads (e.g., photo bytes) need a deliberate
  strategy (see the Data Architecture document's media-handling section) rather than "just let
  the specialist read S3."
- **To revisit:** if a specific specialist genuinely needs broad, ad hoc structured-data querying
  (not just a fixed tool), that's a signal that capability should become its own Tool API, not an
  exception to this boundary.

## Action Items

1. [ ] `AgentCoreStack`'s per-agent execution role (AF-01) grants **no** `dynamodb:*`/`s3:*`
   actions on `AppTable`/`ConnectionsTable`/`MediaBucket`/the agent-state bucket — only
   `bedrock:Retrieve`/`bedrock:GetGuardrail`/etc. and the `InvokeFunctionUrl`/HTTP egress its
   declared tools need.
2. [ ] The orchestrator's execution role is the only specialist-adjacent role with direct
   `AppTable`/`ConnectionsTable`/media-bucket/agent-state-bucket IAM (in addition to
   `bedrock-agentcore:InvokeAgentRuntime` scoped to registered runtimes, per ADR-0012).
3. [ ] Document this boundary in the Data Architecture document
   (`docs/architecture/data-architecture.md`) so it's visible alongside the entity model it
   constrains, not only in this ADR.
4. [ ] CDK assertion test: no specialist execution role/policy includes `dynamodb:` or (outside
   the media-bucket presigned-URL flow that the orchestrator itself performs) `s3:` actions.
