# agents/

Specialist agents built from the **agent factory** (one template, specialized by configuration). Each agent is a self-contained folder with its own `agent.py`, `Dockerfile`, and `requirements.txt`, deployed to Bedrock AgentCore.

- `hello_agent/` — the shared Strands + AgentCore template every `registry/*.json` entry points
  at via its `template` field (originally built to validate the deployment path, TF-04). It is
  no longer deployed under its own name — `registry/` declares which specialists actually run.

Runtimes are deployed via **CDK** (`aws_bedrockagentcore.Runtime`, see [ADR-0005](../docs/architecture/ADRs/0005-agentcore-deployment-via-cdk.md)); the `@aws/agentcore` CLI is used only for local dev (`agentcore dev`) and ad-hoc `invoke`.

Prompts/skills are **externalized** (Bedrock Prompt Management / S3), not embedded here. See [ADR-0001](../docs/architecture/ADRs/0001-use-strands-agents-framework.md).
