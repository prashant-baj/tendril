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

## Files to place manually

The device bridge blocks remote writes to executable-config files. Save these from the chat file
cards into the repo (already generated): `.pre-commit-config.yaml`, `.mcp.json`,
`.github/workflows/ci.yml`. (`Makefile` is optional on Windows — use `tasks.ps1`.)

## Related

- Standards: [`engineering-best-practices.md`](engineering-best-practices.md)
- Architecture: [`architecture/architecture.md`](architecture/architecture.md) · ADRs: [`architecture/ADRs/`](architecture/ADRs/)
- Stories: [`stories/technical-foundation.md`](stories/technical-foundation.md)
