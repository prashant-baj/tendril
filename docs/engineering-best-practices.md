# Engineering Best Practices Checklist

Standards every change to Tendril must meet. Use this as a pre-merge / pre-deploy checklist. Items are grouped; treat anything unchecked as a blocker unless explicitly waived in a PR.

---

## 1. Secrets & Credentials — zero hardcoding

> The #1 rule: **no account IDs, keys, secrets, or credentials in source control — ever.**

- [ ] No AWS **account IDs** committed in code, `cdk.json`, prompts, or docs (inject via CDK context / environment)
- [ ] No AWS **access keys or secret keys** anywhere in the repo
- [ ] No **API keys, tokens, passwords, or connection strings** in code, config, or prompt files
- [ ] AWS access is via **IAM roles** (AgentCore execution role / Lambda execution role) — never long-lived access keys
- [ ] Runtime secrets (WhatsApp/email provider keys, 3rd-party API keys) are read at runtime from **AWS Secrets Manager** or **SSM Parameter Store (SecureString)** — never baked into images or env literals
- [ ] ARNs, bucket names, table names, model IDs are **parameterized per environment**, not hardcoded literals
- [ ] `.env` and local credential files are **git-ignored**; a committed `.env.example` contains **placeholders only**
- [ ] **Secret scanning** runs in CI and as a pre-commit hook (e.g. `gitleaks` / `git-secrets`), plus GitHub **push protection** enabled
- [ ] Least-privilege IAM — scope `Resource` and `Action`; avoid `"*"` wherever feasible
- [ ] Audit any **inherited scaffolding** for hardcoded account IDs, ARNs, or wildcard IAM and parameterize/scope them before first deploy

## 2. Multi-Environment (dev & prod)

- [ ] Single codebase; environment chosen by a parameter (`env_name` ∈ {`dev`, `prod`})
- [ ] Every resource name is **prefixed by environment** (e.g. `tendril-dev-*`, `tendril-prod-*`)
- [ ] Per-environment config lives in **CDK context / SSM**, not in code
- [ ] **Separate AWS accounts** (preferred) or strictly isolated resources for dev vs prod
- [ ] Prod resources use **retain** removal policies for stateful data; dev may `DESTROY`
- [ ] Region is configurable, not hardcoded
- [ ] Dev has **no access** to prod data, endpoints, or secrets

## 3. GitOps & NoOps

- [ ] **Git is the single source of truth** for both application and infrastructure
- [ ] All infrastructure is **code (CDK)** — no console "click-ops"
- [ ] **PR-based** workflow; `main` is always deployable
- [ ] CI/CD **deploys automatically on merge** (e.g. GitHub Actions → `cdk deploy`)
- [ ] Promotion path: merge → **dev** deploy automatically; **prod** deploy behind a tag or manual approval gate
- [ ] CI authenticates to AWS via **OIDC federation** — no static deploy keys stored in the repo/CI secrets
- [ ] **NoOps posture:** use only serverless/managed services (AgentCore Runtime, Lambda, DynamoDB on-demand, EventBridge, S3) — nothing to patch or scale by hand
- [ ] Rollback = redeploy a previous commit (deployments are reproducible from git)

## 4. Monorepo & Selective Deploy

- [ ] Monorepo layout: `agents/`, `app/`, `infra/`, `prompts/`, `docs/`
- [ ] Each deployable unit (agent/service) is a **self-contained folder** with its own `Dockerfile` and `requirements.txt`
- [ ] CI performs **path-based change detection** (`git diff` vs last deployed ref) and **builds/deploys only changed folders**
- [ ] Independent CDK stacks (or constructs) per agent so **unchanged agents are not rebuilt or redeployed**
- [ ] Shared code lives in a **common module**; changes to it trigger dependents intentionally
- [ ] CI job matrix is keyed on changed paths to keep pipelines fast and cheap

## 5. Build with AI (Strands AI-assisted development)

> Leverage [Strands "Build with AI"](https://strandsagents.com/docs/user-guide/build-with-ai/) so the coding assistant works from authoritative Strands docs, not guesswork.

- [ ] Configure the **Strands Agents MCP server** in the coding assistant (`uvx strands-agents-mcp-server`) — Claude Code / Cursor / VS Code / Kiro
- [ ] Commit the MCP config for the chosen tool (e.g. `.cursor/rules/mcp_config.json` or `claude_desktop_config.json`) so the whole team shares it
- [ ] Use **`llms.txt` / `llms-full.txt`** (and per-page `/index.md`) as fallback context when the MCP server isn't available (prefer MCP for token efficiency)
- [ ] Maintain a **project instruction/rules file** (`CLAUDE.md` / `AGENTS.md` / `.cursor/rules`) capturing architecture, tool & agent contracts, naming conventions, and ADR decisions — so AI-generated code stays consistent
- [ ] Treat **ADRs as architectural guardrails for AI-assisted development**: feed the accepted ADRs in `docs/architecture/ADRs/` to the assistant as *binding constraints*, require every generated design and code change to conform to them, and reject anything that silently violates an ADR — a decision may only change by **superseding the ADR first** (new ADR), never by ad-hoc drift in generated code
- [ ] The assistant **must request an ADR before making an architecturally significant change — and must never assume one.** Architecturally significant means: introducing a new service, datastore, queue, or external dependency; a new cross-cutting pattern; a change to a security/auth, data, or tenancy boundary; a change to deployment, integration, or a public contract/API; or anything that conflicts with — or is not yet covered by — an existing ADR. In these cases the assistant must **stop, surface the decision with options and trade-offs, and wait for a human-approved ADR** before implementing. When in doubt, ask rather than assume.
- [ ] Feed the living docs (`project-context.md`, `architecture/architecture.md`, `stories.md`, `architecture/ADRs/`) to the assistant as context for each work item
- [ ] Reference **official Strands examples** for patterns (agents-as-tools, Swarm/Graph, MCP tools, meta-tooling)
- [ ] **Human review + tests** required on all AI-generated code before merge — AI accelerates, it does not approve

## 6. Supporting Quality Gates

- [ ] **Structured logging** with OpenTelemetry / X-Ray; **never log secrets or PII**
- [ ] **Timeouts, retries, and circuit breakers** on every API-backed tool (network calls will fail)
- [ ] **Typed input/output contracts** for tools and agents (this is what makes them pluggable)
- [ ] Tests: **unit** + **tool/agent contract** + **eval scenarios** (behavioral) + **guardrail/accuracy** checks
- [ ] **Resource tagging** (`project=tendril`, `env`, `owner`) for cost visibility
- [ ] **Pinned dependencies** per component; reproducible builds
- [ ] **Pre-commit hooks**: format, lint, secret-scan
- [ ] **ADRs** recorded for every significant technical decision (`docs/adr/`)

---

### Definition of Done (per change)

A change is done when: it passes CI (lint + tests + secret scan), introduces **no hardcoded secrets or account identifiers**, deploys cleanly to **dev** via the pipeline, touches **only the folders it needs**, updates relevant **docs/ADRs**, and has been **reviewed by a human**.
