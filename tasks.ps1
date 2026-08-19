# Tendril task runner for Windows / PowerShell (replaces `make`).
# Usage:  .\tasks.ps1 <task> [-Env dev|prod]
#   e.g.  .\tasks.ps1 synth -Env dev
param(
  [Parameter(Position = 0)][string]$Task = "help",
  [string]$Env = "dev"
)
$ErrorActionPreference = "Stop"

switch ($Task) {
  "install"      { python -m pip install --upgrade pip; python -m pip install pre-commit ruff pytest; pip install -r infra/requirements.txt }
  "hooks"        { pre-commit install }
  "lint"         { ruff check . }
  "format"       { ruff format . }
  "test"         { pytest -q }
  "secrets-scan" { gitleaks detect --no-banner }
  "synth"        { Push-Location infra; cdk synth --context env_name=$Env; Pop-Location }
  "diff"         { Push-Location infra; cdk diff  --context env_name=$Env; Pop-Location }
  "deploy"       { Push-Location infra; cdk deploy --all --context env_name=$Env --require-approval never; Pop-Location }
  "agent-build"  { docker buildx build --platform linux/arm64 -t tendril-hello-agent ./agents/hello_agent }
  default {
    Write-Host "Usage: .\tasks.ps1 <task> [-Env dev|prod]"
    Write-Host "Tasks: install, hooks, lint, format, test, secrets-scan, synth, diff, deploy, agent-build"
  }
}
