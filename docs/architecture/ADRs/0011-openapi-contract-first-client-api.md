# ADR-0011: OpenAPI 3.x contract-first Client API

**Status:** Accepted
**Date:** 2026-09-11
**Deciders:** Project owner / lead engineer

## Context

ADR-0004 decided the Client API's transport (API Gateway + Lambda) and gave an endpoint
table, but no formal contract exists. Two things now make that gap costly:

- The Angular frontend (ADR-0003) is built and its data layer (`GardenApi`/`GoalApi`/`TaskApi`/
  `ActivityApi` in `frontend/src/app/core/services/`) is deliberately mocked behind small
  interfaces, explicitly waiting to be swapped for a real implementation. Without a contract,
  "real implementation" means hand-guessing request/response shapes against whatever the Lambda
  happens to return.
- The backend Lambdas (Client API, ingestion, and now the orchestrator — see ADR-0012) don't
  exist yet beyond `app/README.md`'s placeholder. Building them contract-first, rather than
  inferring a contract from code after the fact, is strictly cheaper right now than later.

## Decision

Adopt **OpenAPI 3.1 (latest 3.x)** as the Client API's contract, and make the spec **the actual
deploy artifact** — not parallel documentation.

- **Location:** `app/api/openapi.yaml`, co-located with the Client API Lambda code (`app/api/`),
  following the existing "self-contained folder per deployable unit" convention.
- **API Gateway is provisioned by importing the spec directly** via CDK's
  `aws_apigateway.SpecRestApi`, using the **REST API (v1)** surface (not `HttpApi`/v2) because
  only v1 supports the full `x-amazon-apigateway-*` vendor extensions this needs: Lambda proxy
  integration, request validation from `components.schemas` (JSON Schema), and CORS — v2 lacks
  spec-driven request validation and several of these extensions.
- Because CDK tokens (a Lambda's `functionArn`, resolved only at synth time) can't live inside a
  static YAML file, the CDK stack **loads the YAML as a dict, walks it to inject each
  operation's `x-amazon-apigateway-integration.uri` with the real Lambda ARN, then constructs
  `SpecRestApi` from the patched dict** via `apigateway.ApiDefinition.from_inline(...)`. This is
  the standard pattern for CDK + spec-driven API Gateway; it's the one place infra code touches
  the spec.
- `components.schemas` defines the domain model (Garden/Plant/Goal/Plan/Task/Tracking/Event —
  architecture.md §2) once, reused across every operation's request/response.
- The same schema doubles as the source for **frontend TypeScript types**
  (`openapi-typescript` or equivalent) — the exact seam the frontend's `Mock*Api` classes were
  built to be swapped at (`frontend/README.md`), so adopting this now avoids a second,
  hand-maintained type definition on the frontend side.

## Options Considered

| Option | Drift risk | API Gateway feature set | Verdict |
|--------|-----------|--------------------------|---------|
| **A. `SpecRestApi` imports the OpenAPI 3.x YAML directly (chosen)** | None — spec *is* the deployed config | Full (request validation, CORS, proxy integration via extensions) | **Chosen** |
| B. OpenAPI as a parallel/generated contract; routes stay imperative CDK (`RestApi` + `addMethod`) | Real — spec and routes can silently diverge unless a strong CI check enforces sync | Full, and more idiomatic per-route CDK (easier auth/VTL customization) | Rejected per explicit ask — more flexible, but only as good as the drift check, which is easy to let rot |
| C. `HttpApi` (API Gateway v2) with OpenAPI import | None if used, but... | Missing: JSON-Schema request validation, several `x-amazon-apigateway-*` extensions | Rejected — cheaper/faster, but the missing spec-driven validation defeats much of the point of being contract-first |
| D. Import the spec into API Gateway out-of-band (console/CLI), CDK unaware | N/A | Full | Rejected — breaks GitOps/IaC; every other resource in this repo deploys via CDK |

## Trade-off Analysis

Option A removes an entire class of bug (contract says one thing, the route does another) at
the cost of authoring in vendor-extension YAML instead of CDK's fluent builder methods, and
needing the synth-time patch step to inject Lambda ARNs. That cost is paid once per new
operation; the payoff (one source of truth, frontend codegen, contract-testable in CI) compounds
as the API grows past the current 8-endpoint table. `HttpApi` (C) is the standard "just use v2,
it's cheaper" instinct, but its whole appeal (simpler, less config) is exactly the spec-driven
validation surface this ADR wants — not a real saving once measured against what's actually
needed.

## Consequences

- **Easier:** one authoritative contract frontend and backend both build against; frontend type
  generation from the same file; CI can lint the spec (e.g. Spectral) and validate example
  payloads before anything is deployed; new operations are additive YAML, not new CDK methods to
  learn.
- **Harder:** authors need to know the `x-amazon-apigateway-*` extensions, not just OpenAPI
  core; the synth-time YAML-patching step is a piece of custom infra code to maintain; per-route
  customization that would be trivial in CDK (a one-off VTL mapping, an unusual auth rule) has to
  be expressed as YAML extensions instead.
- **To revisit:** codegen tooling choice for the frontend TypeScript types (`openapi-typescript`
  vs. alternatives); whether CI enforces spec lint/contract tests as a required check once the
  Client API is actually implemented; Cognito JWT authorizer wiring (ADR-0004's auth seam) as an
  `x-amazon-apigateway-authorizer` extension once auth lands.

> **Refinement (2026-09-12):** "OpenAPI 3.1 (latest 3.x)" as originally decided above turned out
> not to be deployable: API Gateway's REST API (v1) `SpecRestApi`/OpenAPI import only validates
> against **Swagger 2.0 or OpenAPI 3.0.x** — 3.1 is rejected outright. This was discovered via a
> real `cdk deploy` failure on OB-01's first Client API stack (`AWS::ApiGateway::RestApi`
> resource creation: `"Invalid OpenAPI input"`, 400/`InvalidRequest`), not a docs lookup done up
> front. `app/api/openapi.yaml`'s `openapi:` field is corrected to **`3.0.1`**; nothing else in
> this ADR changes — `SpecRestApi`, the synth-time ARN-patch mechanism, and the contract-first
> approach all stand as decided. Any spec content that happens to rely on 3.1-only JSON Schema
> semantics (e.g. `type` as an array including `"null"`) must be written the 3.0.x-compatible way
> instead (`nullable: true`) — none of OB-01's schemas currently do.

## Action Items

1. [ ] Scaffold `app/api/openapi.yaml` (info, servers, `components.schemas` for the domain
   model, paths for ADR-0004's 8 endpoints).
2. [ ] Implement the CDK synth-time YAML-patch + `SpecRestApi` construction (new `ClientApiStack`
   in `infra/stacks/`).
3. [ ] Wire CI spec linting (e.g. Spectral) into `ci.yml`'s `quality` job.
4. [ ] Pick and wire a frontend TypeScript codegen step from the same spec; swap
   `frontend/src/app/core/services/Mock*Api` implementations for real `Http*Api` ones built
   against the generated types.
5. [ ] Add the Cognito JWT authorizer extension once auth (ADR-0004's seam) is implemented.
