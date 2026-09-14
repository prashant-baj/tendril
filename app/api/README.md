# app/api/

The Client API Lambda(s) — see [ADR-0011](../../docs/architecture/ADRs/0011-openapi-contract-first-client-api.md).

- `openapi.yaml` (to be added) — the OpenAPI 3.x contract. **This file is the deploy artifact**,
  not documentation: the CDK stack loads it, patches each operation's
  `x-amazon-apigateway-integration.uri` with the real Lambda ARN, and constructs API Gateway via
  `apigateway.ApiDefinition.from_inline(...)` + `SpecRestApi`. Routes are never defined a second
  time in CDK — the spec is the only source of truth for what the API Gateway resource looks
  like.
- Handler code implementing each operation goes alongside it in this folder, following the
  endpoint table in [`../../docs/architecture/architecture.md`](../../docs/architecture/architecture.md#41-client-api-user-facing).

Not yet scaffolded — this is a placeholder anchoring the convention (see
[ADR-0011](../../docs/architecture/ADRs/0011-openapi-contract-first-client-api.md)'s action
items) ahead of implementation.
