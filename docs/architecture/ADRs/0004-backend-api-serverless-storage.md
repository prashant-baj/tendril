# ADR-0004: Backend — API, Serverless & Storage layers

**Status:** Accepted
**Date:** 2026-08-19
**Deciders:** Project owner / lead engineer

## Context

The application backend must expose the Client API, run **serverless** (NoOps posture — see `../engineering-best-practices.md`), store media and structured state, and — crucially — support **bidirectional communication** between frontend and backend. Agent work is **long-running and asynchronous**: plan proposals, follow-up prompts, and progress updates are produced when ready (seconds to days later), not synchronously on an HTTP request. The tracker also **pushes** reminders. So the backend cannot rely on request/response alone; it must push events to the app (and to out-of-band channels when the user is offline).

**User identification (Auth) is out of scope for now (future)** but the design must leave a clean seam to add it.

## Decision

- **API:** Amazon **API Gateway** (HTTP/REST API) + **AWS Lambda** for the Client API and orchestration triggers.
- **Storage:** **S3** for media (photos, documents, future video) via **presigned upload URLs**; **DynamoDB** for structured domain state + event log; Bedrock Knowledge Bases / S3 for memory (per [ADR-0002](./0002-context-management-and-durable-state.md)).
- **Serverless compute:** Lambda for ingestion, tracker, and notifier; Bedrock AgentCore for the agents (per [ADR-0001](./0001-use-strands-agents-framework.md)).
- **Bidirectional communication:** **API Gateway WebSocket API** as the primary in-app real-time channel (connection IDs stored in DynamoDB), driven by **EventBridge**-routed async events; plus the **notification channel** (WhatsApp/email/web-push) for delivery when the user is offline. The frontend can always reconcile by reading current state from DynamoDB (source of truth), so no message is lost if a socket drops.
- **Media flow:** the frontend requests a **presigned S3 URL** and uploads directly to S3 (handles large files/video without passing through Lambda); an S3 event notifies ingestion.
- **Auth (future):** design the API with an authorizer seam for **Amazon Cognito** (JWT authorizer) and per-tenant scoping; until then use a lightweight/anonymous identifier in dev.

## Options Considered

### Bidirectional communication

#### Option A: API Gateway WebSocket API (chosen)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium — manage connections (connect/disconnect, connection store) |
| Cost | Low — serverless, pay per message/minute |
| Real-time | Yes — true push, bidirectional |
| Fit with stack | High — same API Gateway + Lambda + DynamoDB stack |

**Pros:** True bidirectional push; consistent with the existing serverless stack; connection state in DynamoDB; integrates cleanly with EventBridge-driven async agent events.
**Cons:** Must manage connection lifecycle and reconnection; some plumbing.

#### Option B: AWS AppSync (GraphQL subscriptions)

**Pros:** Managed real-time subscriptions; less connection plumbing; strong typed schema.
**Cons:** Introduces GraphQL as a second API paradigm alongside REST; heavier conceptual footprint for the MVP.

#### Option C: Polling

**Pros:** Trivial.
**Cons:** Poor UX for long-running updates; wasteful cost/latency. Rejected as a primary channel (acceptable only as a fallback).

#### Option D: Server-Sent Events / Lambda response streaming

**Pros:** Simple one-way streaming.
**Cons:** One-directional; less suited to interactive approval/steering. Complementary at best.

### API & storage

REST + Lambda over API Gateway with S3 + DynamoDB is the natural serverless choice consistent with ADR-0001/0002 and the NoOps posture; alternatives (containers/EC2, RDS) were rejected as heavier and unnecessary for the access patterns.

## Trade-off Analysis

The decisive requirement is **asynchronous, long-running work with push updates**. Polling is ruled out for UX/cost; SSE is one-directional. That leaves **WebSocket API vs. AppSync**. AppSync's managed subscriptions are attractive, but adopting GraphQL adds a second API paradigm and conceptual overhead for a team already standardized on REST + Lambda + DynamoDB. **WebSocket API** keeps a single, consistent serverless stack and integrates directly with EventBridge and DynamoDB, at the cost of managing connection lifecycle — an acceptable trade. Because DynamoDB remains the source of truth, the socket is an **optimization for immediacy**, not a correctness dependency: a dropped connection degrades to a state re-fetch, never data loss.

## Consequences

- **Easier:** Real-time UX for async agent work; fully serverless/NoOps; scalable; direct-to-S3 media uploads (large files/video-ready); auth-ready seam.
- **Harder:** WebSocket connection management (connect/disconnect handlers, connection store, reconnection); more moving parts; eventual consistency between push and stored state to handle in the client.
- **To revisit:** Migrating to AppSync if GraphQL is adopted broadly; adding Cognito auth (planned); web-push for browser notifications.

> **Refinement (2026-09-11):** the Client API's contract is now built **contract-first** on
> OpenAPI 3.x (see [ADR-0011](./0011-openapi-contract-first-client-api.md)). The **orchestrator's**
> compute target is refined to **Lambda** (async, EventBridge-triggered, off the Client API's
> request path) rather than AgentCore, per [ADR-0012](./0012-orchestrator-lambda-declarative-agent-registry.md)
> — specialist agents remain on AgentCore, unchanged from ADR-0001. Both refinements narrow this
> ADR's decisions; they don't reverse them.

## Action Items

1. [ ] Implement the Client API (endpoints per [`../architecture.md`](../architecture.md)) on API Gateway + Lambda.
2. [ ] Stand up the **WebSocket API** with a DynamoDB **connection store** and connect/disconnect/route handlers.
3. [ ] Route async agent/tracker events through **EventBridge** to WebSocket push + persistence.
4. [ ] Create **S3** buckets and a **presigned-URL** upload flow (photos/documents, video-ready) with S3-event ingestion.
5. [ ] Define **DynamoDB** tables for domain state, the event log, and WebSocket connections.
6. [ ] Integrate the **notification channel** (WhatsApp/email/web-push) for offline delivery.
7. [ ] Add an **auth seam** (Cognito JWT authorizer placeholder + per-tenant scoping) for future activation.
