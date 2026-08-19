# ADR-0005: Deploy AgentCore runtimes via CDK

**Status:** Accepted
**Date:** 2026-08-19
**Deciders:** Project owner / lead engineer

## Context

Tendril's agents run on Amazon Bedrock AgentCore. There are two ways to provision
an AgentCore **runtime**:

1. The **`@aws/agentcore` CLI** (`agentcore deploy`) — imperative, run by a human/CI step.
2. **AWS CDK** — declarative infrastructure-as-code, alongside the rest of our stacks.

Everything else in Tendril is deployed via CDK with a GitOps, selective-deploy, NoOps
posture (see `../../engineering-best-practices.md`, ADR-0001, ADR-0004). Deploying the
agent runtimes through a separate imperative CLI would fragment the deploy path, break
reproducibility/GitOps, and leave a core resource outside IaC.

CDK now ships a **production L2 construct** for AgentCore: `aws_bedrockagentcore.Runtime`
(backing the `AWS::BedrockAgentCore::Runtime` CloudFormation resource), which builds the
container image from source and provisions the runtime, role, network, and auth config.

## Decision

**Provision AgentCore runtimes with AWS CDK** using `aws_bedrockagentcore.Runtime`. The
`@aws/agentcore` CLI is used **only for local development** (`agentcore dev`) and ad-hoc
`agentcore invoke` — never to provision cloud runtimes.

Implementation (see `infra/stacks/agentcore_stack.py`):
- Container image built from the agent folder via `AgentRuntimeArtifact.from_asset(..., platform=LINUX_ARM64)`.
- A context override `agent_image_uri` selects `from_image_uri` instead (pre-built image) — used by CI and to `cdk synth` without a local Docker build.
- Explicit least-privilege `execution_role`, `environment_variables` (e.g. `MODEL_ID` from context — not hardcoded), public network (VPC available later), `tracing_enabled=True`.
- Runtime names use `[a-zA-Z0-9_]` and are env-prefixed (`tendril_{env}_{name}`).

## Options Considered

| Option | Deploy model | GitOps / IaC | Verdict |
|--------|--------------|--------------|---------|
| **A. CDK `aws_bedrockagentcore.Runtime`** | Declarative | Yes | **Chosen** |
| B. `@aws/agentcore` CLI `deploy` | Imperative | No | Local dev / invoke only |
| C. Hybrid (CDK builds image, CLI deploys runtime) | Mixed | Partial | Rejected — runtime stays outside IaC |
| D. Raw L1 `AWS::BedrockAgentCore::Runtime` / custom resource | Declarative | Yes | Unnecessary now an L2 exists (fallback if L2 lacks a feature) |

## Trade-off Analysis

CDK (A) keeps a **single, declarative deploy path** integrated with the monorepo
selective-deploy pipeline, gives reproducible/rollback-able runtimes, and exposes
execution role, network, and **auth (Cognito/JWT)** declaratively — which lines up with
the future Auth work (ADR-0004). The CLI (B) is excellent for the local inner loop but is
imperative and would create drift and manual steps in prod. The hybrid (C) leaves the most
important resource — the runtime — outside IaC. The L1/custom-resource route (D) is
redundant now that a production L2 construct exists, but remains a fallback for any property
the L2 does not yet expose.

## Consequences

- **Easier:** one IaC deploy path (`cdk deploy`); per-agent runtime as code; selective deploy; declarative auth/network ready for the Auth ADR; CDK hotswap for fast iteration.
- **Harder:** depends on the `aws_bedrockagentcore` L2 module (new — pin `aws-cdk-lib>=2.265.0`); `from_asset` builds the image during synth/deploy, so **Docker is required** locally and in CI (or pass `agent_image_uri` for a pre-built image).
- **To revisit:** drop to the L1 `CfnRuntime` for any unsupported property; add VPC/Cognito config when the Auth ADR lands.

## Action Items

1. [x] Implement `aws_bedrockagentcore.Runtime` in `agentcore_stack.py` (from_asset ARM64 + `agent_image_uri` override).
2. [x] Pin `aws-cdk-lib>=2.265.0` (module availability).
3. [ ] Update CI to build/push the agent image and pass `agent_image_uri`, or build via CDK with Docker in the runner.
4. [ ] Reserve the `@aws/agentcore` CLI for `agentcore dev` / `invoke` only (docs updated).
