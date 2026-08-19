# Tendril — AI Assistant Rules (Build with AI)

This file governs AI-assisted development on Tendril. Read it, the docs it links, and the ADRs **before generating any code**. Use the **Strands Agents MCP docs server** (configured in `.mcp.json`) for authoritative Strands APIs; fall back to `llms.txt` / `llms-full.txt`.

## Read first (context)
- Concept & vision: `docs/project-context.md`
- Architecture: `docs/architecture/architecture.md`
- Decisions (binding): `docs/architecture/ADRs/`
- Standards: `docs/engineering-best-practices.md`
- Backlog: `docs/stories/`

## Architectural guardrails (non-negotiable)
- **ADRs are binding constraints.** All generated designs and code must conform to the accepted ADRs. Do **not** silently violate one.
- **Request an ADR before any architecturally significant change — never assume one.** Architecturally significant = a new service/datastore/queue/external dependency, a new cross-cutting pattern, a change to a security/auth/data/tenancy boundary, a deployment/integration/public-contract change, or anything not yet covered by an ADR. In these cases: stop, present options + trade-offs, and wait for a human-approved ADR.
- A decision changes only by **superseding its ADR** with a new one — not by drift in code.

## Hard rules
- **No hardcoded secrets or AWS account IDs/ARNs.** Use env / CDK context / SSM / Secrets Manager. IAM roles, not access keys.
- **Environments:** single codebase, `env_name` ∈ {dev, prod}; resources env-prefixed.
- **Monorepo + selective deploy:** touch only the folders a change needs.
- **Tools are APIs; agents come from the factory; prompts are externalized.**
- **Tests + human review** required before merge. Model-driven behavior is tested via eval scenarios + tracing, not just unit tests.

## Conventions
- Python 3.13 (ruff format/lint); Node 22 / Angular for frontend.
- Every deployable unit is a self-contained folder with its own `Dockerfile` + `requirements.txt`.
- Update relevant docs/ADRs as part of the change (Definition of Done in the best-practices checklist).
