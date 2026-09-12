# ADR-0014: Package every Client API / Tool Lambda as a container image

**Status:** Accepted
**Date:** 2026-09-12
**Deciders:** Project owner / lead engineer

## Context

OB-01 introduced Tendril's first Client API Lambda (`app/api/garden_handler.py`, deployed by
`infra/stacks/client_api_stack.py`), packaged as a plain zip asset (CDK's
`lambda_.Code.from_asset`). It only depends on `boto3`, which already ships in the Lambda
runtime, so packaging was a non-issue for that one function.

It won't stay that way. AF-03 (tool Lambdas — Weather first, more to follow) and OB-02 (photo
upload/media handling) will add real third-party dependencies to this Lambda tier, and CLAUDE.md
already states a repo-wide convention: *"Every deployable unit is a self-contained folder with
its own `Dockerfile` + `requirements.txt`."* The zip-based `garden_handler` didn't have one,
which was flagged as a live inconsistency rather than silently left in place — this ADR decides
the packaging model for the whole Lambda tier before more Lambdas exist to migrate later.

Note this is a distinct question from ADR-0005 (AgentCore runtime packaging): AgentCore
*mandates* a container image for its runtimes, so that one wasn't a real choice. Regular Lambda
functions genuinely support both zip and container-image packaging — this ADR is the actual
trade-off decision that ADR-0005 didn't have to make.

## Decision

**Every Client API / Tool Lambda function is packaged as a container image**, using CDK's
`aws_lambda.DockerImageFunction` + `DockerImageCode.from_image_asset(...)`, built from AWS's
official `public.ecr.aws/lambda/python:3.13` base image, targeting **ARM64** (matches the
AgentCore runtimes, ADR-0005; cheaper and generally faster than x86_64 for this kind of
workload). Each Lambda's folder gets its own `Dockerfile` + `.dockerignore` + `requirements.txt`
— the same shape as `agents/*/`, satisfying CLAUDE.md's convention without exception anywhere in
the repo.

Synth/test escape hatch, mirroring `AgentCoreStack`'s `agent_image_uri` context override
(ADR-0005): a per-function `<name>_image_repo` / `<name>_image_tag` context pair, when present,
builds the function from `DockerImageCode.from_ecr(...)` (a plain CDK reference to an existing
ECR repository — no local Docker invocation) instead of `from_image_asset` (which shells out to
`docker build` as soon as the construct is instantiated, i.e. during `pytest`, not just
`cdk deploy`). Unit tests always pass this context so `pytest -q` stays fast and
Docker-independent, same as `AgentCoreStack`'s tests today.

## Options Considered

| Option | Cold start (sync, user-facing calls) | Packaging model | Verdict |
|---|---|---|---|
| **A. Container image, every Lambda** | Higher than B for small/light functions | One Dockerfile pattern repo-wide, same as agent runtimes | **Chosen** |
| B. Zip + a shared Lambda Layer for common deps | Lowest | A second packaging model alongside the agents' containers | Rejected — the two-tier packaging story (containers for agents, zip+layers for Lambdas) was explicitly weighed against uniformity and uniformity won |
| C. Case-by-case (zip by default, container only when deps demand it) | Best on average | Undocumented, decided ad hoc per Lambda | Rejected — leaves the next story author guessing instead of following one written rule |

## Trade-off Analysis

Option A's real cost is cold-start latency: container-image Lambdas cold-start slower than zip
for small, frequently-invoked functions, and these Client API Lambdas sit behind synchronous,
user-facing API Gateway calls (e.g. the create-garden button) where that's felt directly. That
cost is accepted here in exchange for **one packaging story for the entire repo** — every
deployable unit (agent or Lambda) is "a folder with a Dockerfile," no exceptions, no second
mental model, no per-story judgment call about whether *this* Lambda's dependencies are "light
enough" for zip. If a specific future Lambda's latency profile turns out to matter enough in
practice to justify revisiting this, that's a reason to supersede this ADR for that Lambda — not
a reason to quietly special-case it.

Docker is already a deploy-time dependency for this repo (the AgentCore runtimes require it,
ADR-0005) — the CI `deploy-dev` job already has `docker/setup-qemu-action` +
`docker/setup-buildx-action`, reused as-is for the new Lambda image builds. No new CI capability
is needed, only adding the new stack to the deploy step and the `app/**` path filter that
triggers it.

## Consequences

- **Easier:** one packaging convention everywhere (`Dockerfile` + `requirements.txt` +
  `.dockerignore` per deployable folder); no shared-Layer versioning/lifecycle to manage; adding
  a heavy/native dependency to a future tool Lambda (e.g. image processing) never requires a
  packaging-model migration, since containers already have headroom up to 10 GB.
- **Harder:** cold start for latency-sensitive, synchronous Client API calls is worse than a zip
  function would have been; every Lambda now needs an ECR-asset-capable CDK bootstrap and Docker
  at deploy time (already true for this repo because of the agents, but now also true for the
  simplest possible Lambda).
- **To revisit:** if a specific Lambda's cold-start latency becomes a measured, user-visible
  problem, consider `SnapStart` (where supported) or superseding this ADR for that one function
  — not a silent per-Lambda exception.

## Action Items

1. [x] Migrate `garden_handler` (OB-01) from zip (`Code.from_asset`) to container image
   (`DockerImageFunction` + `DockerImageCode.from_image_asset`, ARM64), with a
   `garden_handler_image_repo`/`garden_handler_image_tag` context escape hatch for tests.
2. [x] Add `app/api/Dockerfile` + `.dockerignore`.
3. [x] Add `app/**` to the CI `changes` path filter and `tendril-dev-client-api` to the
   `deploy-dev` job's `cdk deploy` stack list (it was never wired in when `ClientApiStack` was
   first added).
4. [ ] Apply the same pattern (Dockerfile + `DockerImageFunction` + image-repo/tag context
   escape hatch) to AF-03's Weather Lambda and OB-02's media Lambda when those stories are
   implemented — this ADR is the reference, not a one-off.
