# ADR-0021: API key access tiers for the Client API and the Evaluation Control API

**Status:** Approved
**Date:** 2026-10-11
**Deciders:** Project owner / lead engineer

> **Imported (2026-11-23) from the GitLab `showcase` / `tendril-group/tendril` repo, text
> unchanged below this note.** That repo shares history with this one and deployed these keys to
> `dev`. Adopted here: the Client API part (decisions 1, 3, 5, 6, 7, 8): keys, usage plans, the
> `x-api-key` security scheme, CORS, and the staged `require_api_key` rollout. **Not adopted yet:**
> the Evaluation Control API itself (decision 2, ADR-0019/0020 live only in the showcase repo).
> Its deployed stack `tendril-dev-eval-control` is represented here by a non-deployed placeholder
> (`infra/stacks/eval_control_stack.py`) so the CI/EVAL key's cross-stack export stays intact.
> On GitHub Actions, "CI/CD variable" below means a repository secret/variable
> (`FRONTEND_API_KEY`), not a GitLab variable. ADR numbers 0016-0031 are reserved for the
> showcase ADRs; this repo's own next ADR after 0021 is 0032.

> **Revision (2026-10-11):** following the owner decision that the evaluation job must not access backend resources directly (including Bedrock), the CI/EVAL key is the only credential the evaluation plane holds: no AWS credential or evaluation AWS role exists, and the judge is reached through the Evaluation Control API (ADR-0019, ADR-0020). The eval key's quota therefore also covers judge calls. The status is unchanged (`Proposed`).

## Context

The Client API (ADR-0004, ADR-0011) is an API Gateway REST API built with `apigateway.SpecRestApi` from `app/api/openapi.yaml`. It has no authentication: `userId` is a caller-supplied identifier (`X-User-Id`), and CORS allows `Content-Type,X-User-Id` with origin `*`. ADR-0004 defers real user authentication and leaves a seam for a Cognito JWT authorizer.

The owner has asked for API key authentication, with **separate keys for the front-end app and for the CI/CD pipeline** (the CI/CD key stored as a pipeline secret), and for **all longitudinal evaluations to run through API calls** (ADR-0019, ADR-0020).

Facts that bound the decision (REST API, not HTTP API, so API keys and usage plans are available):

- An API Gateway API key is **identification plus throttling and quota** (through usage plans). It is **not user authentication** and **not fine-grained authorization**. A usage plan associates a key with API **stages** (optionally with per-method throttling). It **cannot restrict which methods a key may call**. Two keys on one API therefore cannot express "the front end may not call evaluation operations".
- Separation of operations by key must be enforced either (a) **by infrastructure**, with the operations on a separate API or stage whose usage plan contains only the allowed key, or (b) **in handler code**, comparing `requestContext.identity.apiKeyId` (the key ID, never the secret value) with an allow-listed ID from configuration.
- The front-end key necessarily ships in the browser bundle. The deploy-frontend job already substitutes a placeholder (`__CLIENT_API_BASE_URL__`) after the build; a `__CLIENT_API_KEY__` placeholder works the same way. That key is **public by nature**.
- The CI/eval key is a **real secret**. The project is public, so job logs are public. Schedule pipelines run on `main`, where a protected variable is available; fork and merge request pipelines do not receive it.
- Contract-first (ADR-0011) makes `app/api/openapi.yaml` the deploy artifact. Evaluation-only operations must not exist in a public production contract.

Requiring keys on existing Client API routes changes the public contract and an auth boundary, which is architecturally significant per `CLAUDE.md`. This ADR **refines ADR-0004's auth seam without reversing it**: Cognito/JWT user authentication stays the future path.

## Decision

Adopt **separate APIs per key tier**: keys identify and throttle callers on the Client API, and the evaluation operations live on a separate, dev-only API that only the CI/eval key can reach.

1. **Client API requires a key on every operation except CORS `OPTIONS`.** The contract adds `components.securitySchemes` (type `apiKey`, header `x-api-key`) and a `security` requirement on each operation; `OPTIONS` operations declare `security: []`. A single **FRONTEND key** sits in its own usage plan (throttle, and at most a generous quota). It is public and gives identification, throttling and basic abuse control, **not secrecy and not user authentication**.
2. **Evaluation Control API (dev only).** A separate REST API with its **own OpenAPI file, its own handler container image (ADR-0014) and its own usage plan** that contains only the **CI/EVAL key**. It is created only when `env_name == dev` and an explicit CDK flag is set. CDK synth omits it, or fails, for `prod`, and assertion tests enforce this. Because the frontend key is not in that usage plan, the frontend cannot reach evaluation operations. This is **infrastructure-enforced**, not code-enforced. Its operations, including the `judgeTurn` judge operation, are specified in ADR-0019.
3. **CI/EVAL key in the Client API plan too (recommended; owner confirmed 2026-10-13: yes).** The same CI/EVAL key is also associated with a Client API usage plan with its own throttle and quota, so real-cadence evaluation (ADR-0019) exercises the real Client API on the reserved tenant with the real clock, attributable in logs by `apiKeyId`. The key ID differs from the frontend key.
4. **Defense in depth in the Eval API handler.** The handler also compares `requestContext.identity.apiKeyId` to an allow-listed key ID from configuration and rejects anything else. The ID is not a secret.
5. **Key lifecycle.** Keys are created by CDK **without a hardcoded value**; API Gateway is the source of truth. Copying a value into a CI/CD variable is a **manual human step**.
   - **CI/EVAL key:** protected and masked variable `TENDRIL_EVAL_API_KEY`, never in the repo, never echoed. A non-secret variable carries the key ID for audit records. Only `main` and schedule pipelines receive it.
   - **FRONTEND key:** a non-secret CI/CD variable consumed by deploy-frontend to replace `__CLIENT_API_KEY__` at build time (owner confirmed 2026-10-13) (recommended over giving the shared deploy role permission to read key values from API Gateway). The deploy-frontend job would otherwise need `apigateway:GET` with value access on a role that is shared (ADR-0017).
   - **Rotation:** two keys per tier overlap. Create the new key, add it to the usage plan, switch the consumer, then remove the old key. A frontend rotation needs a frontend redeploy.
   - **Who can read a value:** only named humans with API Gateway read permission. CI never reads key values from AWS.
   - **Logging:** access logs and audit records contain `apiKeyId` only. API Gateway access-log formats must never include the `x-api-key` header.
6. **Usage plans bound cost.** The eval key's plan carries a throttle and a **daily quota**, an extra cost-bounding guardrail aligned with the spec cost bounds and set above the harness's own caps (ADR-0020 D5). The quota bounds every call the key can make, including the `judgeTurn` operation (the LLM judge is reached through the Evaluation Control API, so its calls count against the same quota), and so bounds both the system-under-test model spend and the judge's Bedrock spend together with the per-call caps in ADR-0020 D1. The quota must be sized for the arc's calls plus the judge calls and their retries.
7. **CORS.** `OPTIONS` must not require the key. `x-api-key` is added to the allowed CORS headers. API Gateway's default 4XX/5XX gateway responses (missing key 403, throttle 429) carry the CORS headers, so the browser can read them instead of reporting an opaque CORS error.
8. **Environments.** Keys and usage plans exist per `env_name`. `prod` has Client API keys but no Eval API and no CI/EVAL key (owner confirmed 2026-10-13: per-environment keys, no eval key in `prod`).
9. **Verification (implementing changes).** CDK assertions: prod has no Eval API and no eval key; every non-`OPTIONS` Client API operation requires a key; the frontend key is not in the Eval API usage plan; no key value appears in a synthesized template. Contract lint: security scheme present, `OPTIONS` open. Frontend test: requests carry the key header.

## Options Considered

| Option | Separation of eval operations | Fit with the facts | Verdict |
|--------|-------------------------------|--------------------|---------|
| A. No keys (status quo) | None | Evaluation operations would have no gate at all | Rejected |
| B. One shared key for front end and CI | None: the browser key could call anything | Public key doubles as a CI secret; cannot be kept secret | Rejected |
| C. Two keys on one API, handler-side `apiKeyId` checks | Code-enforced only: a missed check exposes evaluation operations on a public prod contract | Works technically, but the prod contract and Lambda carry evaluation operations; a single bug or flag error is a prod exposure | Rejected as the primary control; kept as defense in depth (Decision 4) |
| **D. Separate APIs per key tier, Eval API dev-only (chosen)** | Infrastructure-enforced: the frontend key is not in the Eval API usage plan | Matches the usage-plan model; keeps the prod contract free of evaluation operations (ADR-0011) | **Proposed** |
| E. Cognito/JWT authorizers now (ADR-0004 future work) | Per-user and per-scope | Real user authentication, but needs a user pool, sign-up UX, frontend token handling and machine-client flow for CI; far larger than this need | Not now; still the path to real user auth |
| F. Lambda authorizer mapping keys to scopes | Code-enforced, flexible | A custom authorizer to build, secure, cache and test; keys still public on the front end | Rejected for now; revisit if scopes multiply |

## Trade-off Analysis

D is the only option that makes "the frontend cannot call evaluation operations" true by construction, because a usage plan cannot restrict methods. Its cost is a second API, a second OpenAPI file, a second handler image and an extra deploy unit in `dev`. C is cheaper but keeps evaluation operations in the public contract and relies on every handler check being right.

Being candid about what keys are:

- **The frontend key is public.** Anyone can read it from the bundle. It gives identification, per-key throttling and basic abuse control. It does **not** fix the spoofable `userId`; user data remains unauthenticated until the Cognito/JWT authorizer of ADR-0004 lands.
- **Shared throttling cuts both ways.** All browsers share one frontend key, so one abuser can exhaust the frontend plan for everyone. Keep the frontend plan to throttling with a generous quota; stronger abuse control (a web ACL, per-user limits) is not decided here.
- **The CI/EVAL key is a real secret** and as strong as GitLab's masking and protected-variable handling. Masking is best effort (transformed values are not masked), so the harness must never print headers or request objects (ADR-0020 D6).
- Enabling keys on existing routes is a **breaking change** for any caller without a key. The Client API requirement, the frontend build and deploy-frontend must ship together, or the dev front end breaks in between.

## Consequences

- **Easier:** the evaluation plane needs no AWS credential, only this key; per-caller throttling and quota; a stable, key-ID-attributed trail of who called what; a hard, structural wall between front end and evaluation.
- **Harder:** a leaked eval key can spend judge tokens as well as call the system under test, bounded by the daily quota, per-call caps and rotation; a public contract change (`securitySchemes`, `security`, CORS headers, gateway responses); a second API and image to maintain; manual key-value handling and rotation; the frontend and deploy-frontend change in lockstep with the API.
- **ADR-0004:** the auth seam is refined, not reversed. Cognito/JWT stays the path to user authentication.
- **ADR-0011:** the Client API contract gains a security scheme. The Eval API has its own contract, deployed as a separate artifact; this extends "the contract is the deploy artifact" and does not reverse it.
- **ADR-0013:** the Eval API handler is backend code in the same trusted tier as the Client API handler, in `dev` only. Its role, not CI, holds any Bedrock grant for the judge (ADR-0019).
- **ADR-0017:** unchanged; the shared deploy role gets no new permission under the recommended frontend-key handling.
- **Open questions for the owner:**
  - **ANSWERED (owner, 2026-10-13): yes.** The CI/EVAL key also sits in the Client API usage plan for real-cadence mode (Decision 3). No third key.
  - **ANSWERED (owner, 2026-10-13): yes.** Keys per `env_name`, and no eval key in `prod`.
  - **ANSWERED (owner, 2026-10-13): yes.** The FRONTEND key value is a plain CI/CD variable, seeded into the frontend at build time by deploy-frontend; it is not read from API Gateway.

## Action Items

1. [x] Human review: set this ADR to `Approved` and merge to `main`. Only a human sets the status. (Done.)
2. [ ] After approval, author the OpenSpec change `add-api-key-auth` (Client API security scheme, CORS, frontend key injection, usage plans) and `add-eval-control-api` (ADR-0019). No implementation before then.
3. [ ] Human, later: copy key values into the CI/CD variables (`TENDRIL_EVAL_API_KEY` protected and masked; the frontend key as a plain variable), record key IDs, set usage-plan throttle and quota, and schedule rotation.
4. [ ] Decide separately: web ACL or per-user limits, and the Cognito/JWT authorizer (ADR-0004 future work).
