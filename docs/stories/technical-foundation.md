# Stories — Epic: Technical Foundation (Sprint 0)

Enabler stories to stand up the workspace, tooling, and deployment baseline before feature work. Each story is written to be **build-ready for AI-assisted development**: it has explicit acceptance criteria (the target for generated code + tests) and conforms to the [ADRs](../architecture/ADRs/) and the [engineering best-practices checklist](../engineering-best-practices.md).

**Story format:** `As a <role>, I want <capability>, so that <benefit>` + Acceptance Criteria + Tasks + Dependencies. A story is complete only when it meets the **Definition of Done** in the best-practices checklist (CI green, no hardcoded secrets/accounts, deploys to dev, only-changed-folders, docs/ADRs updated, human-reviewed).

**Suggested order:** TF-01 → TF-02 → TF-03 → TF-04 → TF-05/TF-06 (parallel) → TF-07 → TF-08.

---

## TF-01 — Repository & monorepo structure

**As a** developer, **I want** a monorepo with the agreed layout and tooling baseline, **so that** all components live together and CI can deploy selectively.

**Acceptance Criteria**
- [ ] Top-level folders exist per architecture: `agents/`, `app/`, `infra/`, `prompts/`, `frontend/`, `docs/`.
- [ ] `.gitignore` covers Python, Node/Angular, CDK (`cdk.out`), Docker, and secrets/`.env`.
- [ ] `.editorconfig` and formatter/linter configs are present (Python + TypeScript).
- [x] Pre-commit hooks run format, lint, and **secret-scan** (gitleaks) locally. — `.pre-commit-config.yaml` (ruff system hooks + gitleaks + hygiene).
- [x] `LICENSE` (MIT) and `README.md` present; structure documented.
- [ ] Commit/branch conventions documented (PR-based, `main` deployable).

**Tasks**
- [ ] Scaffold folder structure and placeholder READMEs per module.
- [x] Add `.gitignore`, `.editorconfig`, `.pre-commit-config.yaml` (gitleaks + formatters).
- [ ] Add root workspace/config files for Node and Python components.

**Dependencies:** none. **Status:** ✅ done (Node/Angular workspace files land with the frontend epic).

---

## TF-02 — AWS account & CLI setup (dev/prod)

**As a** developer, **I want** AWS CLI configured with least-privilege, credential-safe profiles and Bedrock access, **so that** I can deploy without hardcoding credentials.

**Acceptance Criteria**
- [ ] AWS CLI v2 installed; named profiles for `dev` (and a plan for `prod`).
- [ ] Auth uses **SSO / short-lived credentials** — no long-lived access keys in the repo or shell history.
- [ ] Default region set; `aws sts get-caller-identity` succeeds for each profile.
- [ ] **Amazon Bedrock model access** (Claude) enabled in the target region.
- [ ] An **AWS Builder ID** exists for the project owner.
- [ ] No account IDs or secrets committed anywhere.

**Tasks**
- [ ] Install/verify AWS CLI v2; configure SSO/profiles.
- [ ] Enable Bedrock model access; note the model IDs/inference profiles for config.
- [ ] Document the setup in `docs/` (developer setup guide).

**Dependencies:** none.

---

## TF-03 — CDK bootstrap & IaC baseline

**As a** developer, **I want** the CDK app bootstrapped and an environment-parameterized stack skeleton, **so that** infrastructure is code and deployable to dev/prod.

**Acceptance Criteria**
- [ ] AWS CDK v2 installed; `cdk bootstrap` completed for the dev account/region (prod planned).
- [ ] `app.py` takes an `env_name` (`dev`/`prod`) and passes it to stacks.
- [ ] Stack skeletons exist (e.g., `infra`, `agentcore`, `api`) with **env-prefixed** resource names.
- [ ] **No hardcoded account IDs/ARNs** — injected via CDK context/env.
- [ ] `cdk synth` and `cdk deploy` succeed for a minimal/"hello" stack.

**Tasks**
- [ ] Install CDK; run bootstrap.
- [ ] Scaffold `app.py` + stack classes; wire `env_name` via context.
- [ ] Deploy a minimal resource (e.g., an S3 bucket) as a smoke test.

**Dependencies:** TF-02.

---

## TF-04 — AgentCore CLI & runtime prerequisites

**As a** developer, **I want** the Strands/AgentCore toolchain and container build prerequisites working, **so that** I can build and deploy an agent runtime.

**Acceptance Criteria**
- [ ] `strands-agents` and `bedrock-agentcore` installed; the AgentCore CLI is available.
- [ ] Docker with **ARM64** build (buildx) works.
- [ ] AgentCore **execution** and **runtime** IAM roles are defined in CDK (least-privilege).
- [ ] A **hello-world agent** image builds and deploys to AgentCore.
- [ ] Invoking the runtime returns a response; logs reach CloudWatch; OpenTelemetry is enabled.

**Tasks**
- [ ] Install/verify the AgentCore CLI and container toolchain.
- [ ] Define exec/runtime roles (parameterized, no wildcards where avoidable).
- [ ] Build + deploy a hello agent; smoke-test invoke and trace.

**Dependencies:** TF-02, TF-03.

---

## TF-05 — Build with AI setup (Strands MCP + rules)

**As a** developer, **I want** the Strands "Build with AI" tooling wired into the coding assistant, **so that** AI-assisted development is grounded in authoritative docs and governed by our ADRs.

**Acceptance Criteria**
- [x] The **Strands Agents MCP docs server** is configured (`uvx strands-agents-mcp-server`) in `.mcp.json` and committed for the team.
- [x] `llms.txt` / `llms-full.txt` referenced as fallback context — repo `llms.txt` links the canonical Strands indexes; `CLAUDE.md` points to it.
- [x] A project rules file (`CLAUDE.md` / `AGENTS.md`) exists capturing architecture, tool/agent contracts, conventions, and **ADRs as binding guardrails**, including the "**request an ADR before any architecturally significant change**" rule.
- [ ] MCP verified (Inspector or a test query returns Strands docs). — run locally: `uvx strands-agents-mcp-server` requires `uv` installed.

**Tasks**
- [x] Add MCP config file(s). — `.mcp.json` + repo `llms.txt` fallback.
- [x] Author `CLAUDE.md`/`AGENTS.md` linking `project-context.md`, `architecture/`, and `ADRs/`.
- [ ] Verify the assistant retrieves Strands docs via MCP.

**Dependencies:** TF-01. **Status:** ✅ done (config committed; one-time local MCP smoke-test pending `uv`).

---

## TF-06 — Local development environment

**As a** developer, **I want** a reproducible local environment with pinned runtimes, **so that** all contributors and the AI assistant build consistently.

**Acceptance Criteria**
- [ ] Python 3.13 (+ venv) and Node LTS (+ npm) installed; **Angular CLI**, **Docker**, and **uv/uvx** available.
- [ ] Versions pinned and documented; per-component `requirements.txt` / `package.json`.
- [ ] Convenience scripts (`make`/npm scripts) for build, test, lint, deploy.
- [ ] A developer setup guide in `docs/` reproduces the environment from scratch.

**Tasks**
- [ ] Document prerequisites and pin versions.
- [ ] Add setup/bootstrap scripts.
- [ ] Verify a hello build for both a backend component and the Angular app.

**Dependencies:** TF-01.

---

## TF-07 — CI/CD & selective deploy (GitOps)

**As a** developer, **I want** a CI pipeline with change detection, secret scanning, tests, and OIDC-authenticated CDK deploy, **so that** only changed folders deploy to dev on merge with no static keys.

**Acceptance Criteria**
- [ ] CI (GitHub Actions) runs on PRs: **lint, test, gitleaks** — required status checks.
- [ ] CI authenticates to AWS via **OIDC** (no static deploy keys).
- [ ] **Path-based change detection** deploys only changed stacks/folders via CDK.
- [ ] Merge to `main` auto-deploys to **dev**; **prod** is gated by tag/approval.
- [ ] Branch protection enabled on `main`.

**Tasks**
- [ ] Create the CI→AWS OIDC role in CDK.
- [ ] Author the workflow with path filters and per-path deploy jobs.
- [ ] Configure branch protection and required checks.

**Dependencies:** TF-03.

---

## TF-08 — Secrets & configuration baseline

**As a** developer, **I want** secrets and env config managed via SSM/Secrets Manager and CDK context, **so that** the no-hardcoding rule is enforced from day one.

**Acceptance Criteria**
- [ ] `.env.example` with **placeholders only**; real `.env` git-ignored.
- [ ] Runtime secrets stored in **Secrets Manager / SSM Parameter Store** and read at runtime.
- [ ] Per-environment config via **CDK context** (no env values in code).
- [x] gitleaks runs in **pre-commit** (`.pre-commit-config.yaml`) **and CI** (`ci.yml`); GitHub push protection enabled account-side.
- [ ] Verified: no account IDs, keys, or secrets anywhere in the repo.

**Tasks**
- [ ] Scaffold parameter/secret entries and a config-loading helper.
- [ ] Add `.env.example`; wire secret retrieval.
- [ ] Confirm scans pass and inherited scaffolding is audited for hardcoded values.

**Dependencies:** TF-01, TF-03. **Status:** ✅ done (secret retrieval helper in `app/common/config.py`; push protection is account-side).

---

## TF-09 — Turn on Client API key enforcement (ADR-0021 staged rollout)

**As a** project owner, **I want** the Client API to reject requests without the FRONTEND API key,
**so that** every caller is identified and throttled by its usage plan instead of the API being
open to anyone who finds the URL.

**Context.** The keys and usage plans already exist in `dev` (ported from the showcase repo
2026-11-23, [ADR-0021](../architecture/ADRs/0021-api-key-access-tiers.md)), and the contract
declares `x-api-key` on every operation. But `ClientApiStack` strips the requirement while the
CDK flag `require_api_key` is false, which is the dev default. The key value only exists after a
deploy, so the rollout must happen in this order, or the dev front end breaks in between.
The FRONTEND key is **public by nature** (it ships in the browser bundle). It is identification
and throttling, **not** user authentication; that stays the Cognito path (ADR-0004, CP-11).

**Acceptance Criteria**
- [ ] **Step 1 — secret.** A human copies the `tendril-dev-frontend` key value (API Gateway
  console, or `aws apigateway get-api-key --api-key <FrontendApiKeyId> --include-value`) into the
  GitHub repository variable/secret `FRONTEND_API_KEY`. CI never reads key values from AWS
  (ADR-0021 decision 5); the value is never committed or echoed in logs.
- [ ] **Step 2 — frontend sends the key.** A `CLIENT_API_KEY` DI token defaults to the
  placeholder `__CLIENT_API_KEY__`, and an HTTP interceptor adds `x-api-key` to every Client API
  request (and only those). Port both from the showcase repo
  (`frontend/src/app/core/config/client-api.config.ts`,
  `frontend/src/app/core/interceptors/api-key.interceptor.ts` + spec). The deploy-frontend step in
  `.github/workflows/ci.yml` substitutes `__CLIENT_API_KEY__` from `FRONTEND_API_KEY` exactly like
  `__CLIENT_API_BASE_URL__`, and fails the job if the variable is empty (never ship the
  placeholder).
- [ ] Step 2 is deployed and verified **before** step 3: the live site sends the header while the
  API still doesn't require it (no breakage window).
- [ ] **Step 3 — enforce.** Set `require_api_key: true` for dev (CDK context in `infra/cdk.json`,
  or `--context require_api_key=true` in the deploy step) and redeploy `tendril-dev-client-api`.
  The existing CDK test (`test_every_non_options_operation_requires_a_key_when_enforced`) covers
  the synthesized contract.
- [ ] **Verified in dev:** a request without `x-api-key` gets **403** with CORS headers (readable
  by the browser, via the Default4xx gateway response); the web app works end to end; the CORS
  preflight still succeeds without a key; a burst beyond the plan's throttle gets **429**.
- [ ] Rotation note recorded (ADR-0021 decision 5): create a second key → add it to the plan →
  update `FRONTEND_API_KEY` + redeploy the frontend → remove the old key.

**Tasks**
- [ ] Port `CLIENT_API_KEY` + `api-key.interceptor` (+ spec) and register the interceptor in
  `app.config.ts`.
- [ ] CI: add the `__CLIENT_API_KEY__` substitution + empty-value guard to the frontend deploy.
- [ ] Human: add the `FRONTEND_API_KEY` GitHub variable/secret.
- [ ] Deploy steps 1–2, verify, then flip `require_api_key` and deploy step 3; smoke test.
- [ ] Docs: `architecture.md` §4.1 (enforcement on), backlog, `developer-setup.md` (local dev
  needs the key in the dev build or a local override).

**Dependencies:** ADR-0021 port (done 2026-11-23), TF-07 (CI/CD), TF-08 (secrets baseline).
**Blocking ADR:** none — ADR-0021 (Approved) already decides this; this story only executes its
staged rollout. **Status:** ☐ to do.

---

### Definition of Done (applies to every story)

Per [`../engineering-best-practices.md`](../engineering-best-practices.md): CI green (lint + tests + secret scan), **no hardcoded secrets/account identifiers**, deploys cleanly to **dev** via the pipeline, touches **only the folders it needs**, updates relevant **docs/ADRs**, and is **human-reviewed**.

### Next epics (not in this file)

Walking skeleton (photo → orchestrator → response), agent factory + prompts, orchestrator & specialists, tools/APIs, tracker/outcome loop, frontend app, notifications. To be drafted as feature stories.
