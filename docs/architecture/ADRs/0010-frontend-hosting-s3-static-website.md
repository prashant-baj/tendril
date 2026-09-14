# ADR-0010: Frontend hosting — S3 static website, deployed on push

**Status:** Accepted
**Date:** 2026-09-11
**Deciders:** Project owner / lead engineer

## Context

The Angular frontend (`frontend/`, ADR-0003) now exists and needs to actually be hosted
somewhere, deployed automatically from CI on every push to `dev`/`main` that touches
`frontend/**` — the same GitOps, selective-deploy posture as every other stack
(`docs/engineering-best-practices.md`, ADR-0008's `ci.yml` path-filter pattern).

This is architecturally significant per `CLAUDE.md` — it's a new AWS resource plus a new
**public-facing** surface, which none of Tendril's existing S3 usage is: `FoundationStack`'s
media bucket is fully private (`BLOCK_ALL`, accessed only via presigned URLs). Hosting a static
site necessarily means *some* form of public read access, which is a real departure from that
posture and worth calling out explicitly rather than letting it drift in as "just another
bucket."

One fact shaped the options: ADR-0003 makes this app a **PWA with a service worker**, and
service workers only register in a [secure context](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts)
(HTTPS, or `localhost`). Plain S3 static-website hosting serves over **HTTP only** — so this
decision knowingly trades away the PWA's installability/offline behavior in production until a
later HTTPS front (e.g. CloudFront) is added.

## Decision

Host the built Angular app on **plain S3 static website hosting**, publicly readable, deployed
by CI on every push that touches `frontend/**`.

- New `FrontendStack` (`infra/stacks/frontend_stack.py`), following the existing per-concern
  stack pattern (`foundation`/`prompts`/`guardrails`/`agentcore`/`pipeline`): one env-prefixed
  bucket (`tendril-{env}-web`), `website_index_document` and `website_error_document` both set
  to `index.html` (the Angular Router uses HTML5 `pushState`, so any deep link — e.g. `/tasks` —
  must fall back to `index.html` for the client-side router to take over; S3's error-document
  mechanism does this, at the cost of those requests reporting HTTP 404 in the browser's network
  tab even though the app renders correctly).
- `public_read_access=True` with `block_public_access=BLOCK_ACLS` (blocks legacy ACL-based
  public grants, allows the bucket-policy-based public `GetObject` that static hosting needs) —
  the narrowest public-access shape CDK offers for this use case.
- Wired into `infra/app.py` alongside the other stacks.
- `ci.yml`: a `frontend` path filter (`frontend/**`) joins the existing
  infra/agents/prompts/guardrails filters and the `deploy-dev` trigger condition;
  `tendril-dev-frontend` is added to the standard `cdk deploy` stack list (always listed, like
  the others — CDK no-ops unchanged stacks); then, only when `frontend` changed, CI runs
  `npm ci && npx ng build` and `aws s3 sync frontend/dist/tendril-web/browser/ s3://tendril-dev-web --delete`.
- `PipelineStack`'s `GithubDeployRole` gets a narrowly-scoped `s3:PutObject`/`DeleteObject`/`ListBucket`
  grant on `arn:aws:s3:::tendril-*-web(/*)` for that sync step — it runs as a plain AWS CLI call
  outside CDK, so it needs its own permission, resolved by the same stable-naming convention
  (no CFN cross-stack import) ADR-0008 already established for guardrails/prompts.

## Options Considered

| Option | HTTPS / PWA | Public-access shape | Complexity | Verdict |
|--------|-------------|----------------------|------------|---------|
| **A. Plain S3 static website hosting** | ❌ HTTP only — breaks the service worker in prod | Bucket policy public `GetObject` | Lowest | **Chosen** — explicitly requested; simplest, cheapest, ships today |
| B. S3 (private) + CloudFront (OAC) | ✅ Free HTTPS on `*.cloudfront.net` | Bucket stays fully private, matching every other bucket in this repo | Medium (distribution, OAC, cache invalidation on deploy) | Rejected for now — recommended as the natural upgrade path; revisit once the PWA/offline story matters in practice |
| C. AWS Amplify Hosting | ✅ Managed HTTPS + CI built in | Managed, opinionated | Low setup, but a second deploy system running alongside our own CDK/GitOps pipeline | Rejected — fragments the deploy path the same way ADR-0005 rejected the `agentcore` CLI for runtimes; everything else here is CDK + our own `ci.yml` |

## Trade-off Analysis

Option A was chosen knowingly, not by default: it's the simplest and cheapest path, and it ships
the app today. Its real cost is giving up HTTPS, which means the service worker registered in
`app.config.ts` will fail to register on the deployed site (the app itself still works fine as a
plain web page — this only affects installability/offline support). Option B is the "correct"
long-term answer and requires no application changes to adopt later (CloudFront in front of the
*same* bucket, switched to `OAI`/OAC and `BLOCK_ALL`) — it's noted here explicitly so it isn't
forgotten. Option C was rejected on the same grounds ADR-0005 already established for agent
runtimes: one deploy path (CDK + our GitHub Actions pipeline), not two.

## Consequences

- **Easier:** the frontend deploys automatically on every relevant push, no manual steps; the
  stack follows the exact shape of every other stack in `infra/stacks/`, so it's easy to read
  and maintain; no CloudFront cost or cache-invalidation step to manage yet.
- **Harder:** the deployed site is HTTP-only, so **the PWA install prompt / offline support will
  not work in production** until Option B is adopted — this is a known, accepted limitation, not
  an oversight. Deep-link routes report a 404 status in the browser network tab even though they
  render correctly (S3 error-document behavior).
- **To revisit:** move to Option B (S3 private + CloudFront) once the PWA/offline behavior
  actually matters, or once a custom domain is wanted (which needs CloudFront + ACM anyway);
  add a prod deploy path (gated, per ADR the same way `ci.yml`'s existing prod-gate TODO is
  tracked).

## Action Items

1. [x] `FrontendStack` (S3 static website, public read via bucket policy, `BLOCK_ACLS`).
2. [x] Wire into `infra/app.py`.
3. [x] `ci.yml`: `frontend` path filter, `tendril-dev-frontend` in the deploy stack list,
   conditional `npm ci && ng build` + `aws s3 sync --delete` steps.
4. [x] `PipelineStack`: scoped S3 sync permissions for `GithubDeployRole`.
5. [x] CDK assertion test for the bucket's website config + public-read policy.
6. [ ] Follow-up: CloudFront + OAC in front of the same bucket (Option B) when the PWA/offline
   story is prioritized.
7. [ ] Follow-up: prod deploy path (tag/approval gate) — same open item `ci.yml` already tracks
   for the other stacks.
