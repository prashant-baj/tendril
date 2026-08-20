# Prepare Dev Environment (runbook)

Windows-first (PowerShell) runbook to prepare the Tendril development environment.
Steps marked **[you]** run on your machine / AWS account; the rest is scaffolded in the repo.

> **Conventions (Windows/PowerShell)**
> - Task runner: `.\tasks.ps1 <task> -Env dev` (replaces `make`).
> - Env vars: `$env:VAR = "value"` (not `export`).
> - Scripts: `.\scripts\verify-aws.ps1`.

---

## 1. Install prerequisites **[you]**

Using **winget** (run PowerShell as your user):

```powershell
winget install Amazon.AWSCLI --accept-source-agreements --accept-package-agreements
winget install OpenJS.NodeJS.LTS        # Node 22 LTS
winget install Python.Python.3.13
winget install Docker.DockerDesktop      # ARM64 image builds (buildx)
winget install astral-sh.uv              # required by the Strands MCP server + AgentCore CLI
```

Global npm + Python tools:

```powershell
npm install -g aws-cdk @angular/cli @aws/agentcore
pip install pre-commit ruff pytest
```

`gitleaks` (secret scanning) via scoop or choco, e.g.: `scoop install gitleaks` **or** `choco install gitleaks`.

## 2. Update the AWS CLI (v2) **[you]**

```powershell
winget upgrade Amazon.AWSCLI --accept-source-agreements --accept-package-agreements
# or re-run the official MSI (installs/updates in place), then reopen PowerShell:
#   msiexec.exe /i https://awscli.amazonaws.com/AWSCLIV2.msi
aws --version          # expect aws-cli/2.x
```

## 3. Install / update the AgentCore CLI **[you]**

Tendril uses the current **`@aws/agentcore`** CLI (npm). The older pip
`bedrock-agentcore-starter-toolkit` is legacy — uninstall it if present to avoid a
command collision.

```powershell
npm install -g @aws/agentcore     # requires Node 20+ (you have 22) and uv
agentcore --version
# later self-updates:
agentcore update
# if the legacy pip toolkit was installed:
#   pip uninstall -y bedrock-agentcore-starter-toolkit
```

Main commands: `agentcore create`, `agentcore dev` (local hot-reload), `agentcore deploy`, `agentcore invoke`.

> **Note (tooling):** `@aws/agentcore` is the **CLI** for deploy/invoke; the
> `bedrock-agentcore` package in `agents/hello_agent/requirements.txt` is the **runtime SDK**
> used inside `agent.py` — keep it. The division of deploy duties between this CLI and our CDK
> (which provisions S3/DynamoDB/roles) is worth recording as an ADR when finalized.

## 4. Repo bootstrap **[you]**

```powershell
.\tasks.ps1 install     # python dev tooling + infra deps
.\tasks.ps1 hooks       # install pre-commit hooks (format, lint, secret-scan)
Copy-Item .env.example .env   # fill NON-secret values only
```

## 5. AWS account setup **[you]**

1. Configure SSO / a named profile `tendril-dev` — **no long-lived keys**:
   `aws configure sso` then `aws sso login --profile tendril-dev`.
2. Create an **AWS Builder ID** (needed for the hackathon submission).
3. Enable **Amazon Bedrock model access** for Claude in your region (console → Bedrock → Model access).
4. Verify:

```powershell
$env:AWS_PROFILE = "tendril-dev"
.\scripts\verify-aws.ps1
```

## 6. CDK bootstrap & deploy (dev) **[you]**

```powershell
$env:CDK_DEFAULT_ACCOUNT = (aws sts get-caller-identity --profile tendril-dev --query Account --output text)
$env:CDK_DEFAULT_REGION  = "ap-south-1"   # Mumbai
# set github_org / github_repo in infra/cdk.json first
cd infra; cdk bootstrap --context env_name=dev; cd ..
.\tasks.ps1 synth -Env dev
.\tasks.ps1 deploy -Env dev      # foundation + agentcore + pipeline stacks
```

## 7. Hello agent → AgentCore (via CDK) **[you]**

The AgentCore runtime is deployed by **CDK** (ADR-0005): `cdk deploy` builds the ARM64
image from `agents/hello_agent` and provisions the runtime — **Docker Desktop must be
running**. The model id is pre-set in `infra/cdk.json` context to a Mumbai **global**
cross-region Claude profile (`global.anthropic.claude-sonnet-4-6`) — verify it is enabled
in your account (`aws bedrock list-inference-profiles --region ap-south-1`) and adjust if needed.

```powershell
# with Docker running (builds image + deploys):
.\tasks.ps1 deploy -Env dev

# CI / no local Docker — deploy a pre-built image instead:
cd infra
cdk deploy tendril-dev-agentcore --context env_name=dev `
  --context model_id="global.anthropic.claude-sonnet-4-6" --context agent_image_uri="<ecr-image-uri>"
cd ..

# local inner loop only (not for provisioning):
agentcore dev            # hot-reload;  `agentcore invoke` for ad-hoc calls
```

## 8. Build with AI **[you]**

- `.mcp.json` configures the Strands MCP docs server (`uvx strands-agents-mcp-server`); ensure `uv` is installed.
- `CLAUDE.md` carries the architecture, contracts, and **ADR guardrails** — the assistant must read it and **request an ADR before any architecturally significant change**.

## 9. Frontend scaffold **[you]**

See `frontend/README.md` for the one-time `ng new` + `@angular/pwa` + `@angular/material` commands.

## 10. CI/CD (GitHub) **[you]**

- Deploy the `pipeline` stack to create the OIDC deploy role; copy its ARN.
- In GitHub repo settings: add secret `AWS_DEPLOY_ROLE_ARN`, variable `AWS_REGION`, enable branch
  protection on `main` with required status checks, and turn on secret push protection.

---

## Deployment & CI Gotchas

Real issues hit during first-time setup, with the fix for each. Ordered by where you'll meet them.

### Toolchain / CDK version skew
- **`ImportError: cannot import name 'aws_bedrockagentcore' from 'aws_cdk'`** — `aws-cdk-lib` is too old (the AgentCore L2 landed in ~2.265). Upgrade: `python -m pip install --user --upgrade "aws-cdk-lib>=2.265.0" "constructs>=10,<11"`.
- **"This CDK CLI is not compatible with the CDK library… schema version 54.0.0 > 48… need CLI 2.1137.0"** — the CLI is older than the library. Upgrade: `npm install -g aws-cdk@latest`. Rule of thumb: keep the **library (pip)** and **CLI (npm)** both current.

### AgentCore deploy
- **`AWS::Logs::Delivery` CREATE_FAILED — "X-Ray Delivery Destination is supported with CloudWatch Logs as a Trace Segment Destination"** — the account's X-Ray trace-segment destination isn't CloudWatch Logs. We default managed tracing **off** (context `tracing`). To turn it on: `aws xray update-trace-segment-destination --destination CloudWatchLogs --region ap-south-1`, then deploy with `--context tracing=true`. (Container OTEL traces still emit regardless.)
- **Stack stuck in `ROLLBACK_COMPLETE`; redeploy fails** — a failed *create* can't be updated. Delete then redeploy: `aws cloudformation delete-stack --stack-name tendril-dev-agentcore --region ap-south-1` → `aws cloudformation wait stack-delete-complete …` → `cdk deploy …`.
- **Pipeline stack fails: GitHub OIDC provider already exists** — reuse it: `cdk deploy tendril-dev-pipeline … --context github_oidc_provider_arn=arn:aws:iam::<acct>:oidc-provider/token.actions.githubusercontent.com`.

### Bedrock models (Mumbai / ap-south-1)
- **`AccessDeniedException: … INVALID_PAYMENT_INSTRUMENT: A valid payment instrument must be provided`** — Anthropic (Claude) models are **AWS Marketplace subscriptions** that require a valid payment method on the account. **Promo credits alone do not satisfy this** — add a card under Billing → Payment preferences, or use a first-party Amazon model for testing.
- **Subscribed to one model, calling another** — e.g. an active **Sonnet 4** subscription but calling **Sonnet 4.6** (a *separate* subscription) → AccessDenied. Point `model_id` at a model you're actually subscribed to.
- **`amazon.titan-text-express-v1` — "marked by provider as Legacy… not actively using in the last 30 days"** — Titan Text is retired. Use current-gen **Nova**.
- **`ValidationException: … on-demand throughput isn't supported. Retry with … an inference profile`** — Nova needs an **inference profile**, not the bare model id. Mumbai uses **global** profiles, e.g. `global.amazon.nova-2-lite-v1:0`. List them: `aws bedrock list-inference-profiles --region ap-south-1`.
- **Claude in Mumbai** — use **global** cross-region inference profiles (`global.anthropic.*`); still needs the payment instrument above.

### Invoking the runtime (Windows / PowerShell)
- **`Invalid JSON in request… Expecting property name enclosed in double quotes`** — PowerShell strips the double quotes from `--payload '{"prompt":"…"}'`. Write the payload to a file and pass it as binary: `Set-Content -Path payload.json -Value '{"prompt":"…"}' -NoNewline -Encoding ascii` then `--payload fileb://payload.json`.
- **`runtimeSessionId` must be ≥ 33 characters.**

### PowerShell here-strings
- **Broken YAML/JSON generated from a here-string** — a single-quoted here-string (`@'…'@`) is **fully literal**; do **not** double single quotes (`''` was written verbatim and broke a workflow expression). Use plain single quotes inside.

### GitHub Actions / OIDC (the ones that cost the most time)
- **Push doesn't trigger the workflow** — the trigger only listed `main`. Add your branch: `push: branches: [main, dev]` (and include it in the deploy `if:`), or open a PR into `main`.
- **`.github/workflows/*.yml`, `Makefile`, `.mcp.json`, `.pre-commit-config.yaml` can't be written by the device bridge** — create them locally (e.g. via a PowerShell here-string) and commit.
- **`Input required and not supplied: aws-region`** — set a repository **Variable** `AWS_REGION=ap-south-1` (referenced as `vars.`, *not* `secrets.`).
- **`Could not load credentials… no role-to-assume`** — the action got no role ARN, i.e. `secrets.AWS_DEPLOY_ROLE_ARN` resolved empty. It must be a **repository Secret** (not a Variable, not only environment-scoped), named **exactly** `AWS_DEPLOY_ROLE_ARN`, on the **correct repo**. Trigger a **fresh commit** — re-running an old run can use stale secret state.
- **`Not authorized to perform sts:AssumeRoleWithWebIdentity`** — trust-policy mismatch. Verify all three: Principal = your GitHub OIDC provider ARN; condition `…:aud = sts.amazonaws.com`; and the `…:sub` matches. **The big trap: the immutable-ID subject.** GitHub may emit `repo:OWNER@OWNERID/REPO@REPOID:…` instead of `repo:OWNER/REPO:…`, so a plain `repo:owner/repo:*` condition never matches. Either match the ID form (e.g. `repo:prashant-baj@26347728/tendril@1339150880:*`) or reset the repo's subject customization to default: `gh api --method PUT repos/OWNER/REPO/actions/oidc/customization/sub -f use_default=true`. Also mind the sub **type**: a job with `environment: dev` produces `…:environment:dev`, while a plain branch push produces `…:ref:refs/heads/<branch>`.
- **Reusing a deploy role from another project** — its permissions may not cover CDK. The role needs at least `sts:AssumeRole` on `arn:aws:iam::<acct>:role/cdk-*`. Prefer the dedicated `tendril-dev-pipeline` role, which has the right trust and permissions.

### Lint (CI `quality` job)
- **`EXE001 Shebang is present but file is not executable`** and **`I001 Import block is un-sorted`** — the repo ships `ruff.toml` (selects E4/E7/E9/F/I, `combine-as-imports`, EXE not selected). Run `ruff check --fix . ; ruff format .` and commit before pushing.

### Harmless noise
- **"Node 20 is being deprecated… running with Node 24 by default"** — an informational GitHub Actions notice, not an error. Ignore it.

## Files to place manually

The device bridge blocks remote writes to executable-config files. Save these from the chat file
cards into the repo (already generated): `.pre-commit-config.yaml`, `.mcp.json`,
`.github/workflows/ci.yml`. (`Makefile` is optional on Windows — use `tasks.ps1`.)

## Related

- Standards: [`engineering-best-practices.md`](engineering-best-practices.md)
- Architecture: [`architecture/architecture.md`](architecture/architecture.md) · ADRs: [`architecture/ADRs/`](architecture/ADRs/)
- Stories: [`stories/technical-foundation.md`](stories/technical-foundation.md)
