# ADR-0006: Externalize prompts via Bedrock Prompt Management

**Status:** Accepted
**Date:** 2026-08-20
**Deciders:** Project owner / lead engineer

## Context

Prompts were hardcoded in agent code (the hello agent's `SYSTEM_PROMPT`). Tendril's
thesis (see `project-context.md`, ADR-0001) is that **prompts are the application** and
should be **externalized** so domain experts can own and version them per specialty
without touching code. Two sanctioned mechanisms exist: files in **S3**, or **Amazon
Bedrock Prompt Management**.

## Decision

Externalize prompts using **Bedrock Prompt Management**. Each prompt is provisioned as
IaC (`AWS::Bedrock::Prompt` + `AWS::Bedrock::PromptVersion` via CDK `CfnPrompt` /
`CfnPromptVersion`, consistent with the "everything via CDK" posture of ADR-0005). Agents
receive the prompt identifier and a **pinned version** as environment variables
(`PROMPT_ID`, `PROMPT_VERSION`) and fetch the template at runtime via the `bedrock-agent`
**`GetPrompt`** API, reading `variants[].templateConfiguration.text.text`. Agents keep a
**built-in default** and fall back to it if the fetch fails, so prompt loading never takes
the agent down. The execution role is granted `bedrock:GetPrompt` scoped to the prompt ARN.

## Options Considered

| Option | Versioning / governance | Setup | Verdict |
|--------|-------------------------|-------|---------|
| **A. Bedrock Prompt Management** | Native immutable versions, managed console for experts | Medium (resources + GetPrompt + fetch) | **Chosen** |
| B. S3 files | None built-in (you build it) | Low | Simpler, but no native versioning/governance |
| C. Inline in code (status quo) | None | None | Rejected — defeats externalization |

## Trade-off Analysis

Prompt Management directly serves the **expert-configured-agents moat**: experts edit and
version prompts in a managed console, changes are immutable and auditable, and the mechanism
is AWS-native and CDK-provisionable. The cost is more moving parts than S3 (a prompt resource,
a version, a `GetPrompt` call, and IAM), and — because the runtime pins a **version** — changing
prompt text requires publishing a new version and updating the runtime's env var (a redeploy).
That determinism is acceptable and desirable for now; live-editing can be added later by
pointing at the `DRAFT` version or adding a refresh path. S3 was rejected as the primary
mechanism because it lacks native versioning/governance; inline is rejected outright.

## Consequences

- **Easier:** versioned, governed, expert-editable prompts; per-agent prompt; fully CDK-managed; no code change to update prompt text.
- **Harder:** agents need `bedrock:GetPrompt` and a fetch-with-fallback path; a pinned version means a redeploy to change text; the prompt fetch adds a cold-start call.
- **To revisit:** caching/refresh strategy (cold-start vs. per-invoke vs. TTL); `DRAFT` vs. pinned version for live edits; a dedicated **prompts stack** and per-specialty naming convention as the catalog grows.

## Deployment topology (refinement, 2026-08-20)

Prompts are provisioned in a **dedicated `PromptsStack`**, separate from the runtime
(`AgentCoreStack`). Agents reference a prompt only by its **stable name** (`PROMPT_NAME`,
e.g. `tendril-dev-hello-system`) and resolve the id at runtime — there is **no CloudFormation
cross-stack import**. This keeps the stacks decoupled: prompt changes deploy on their own
(`cdk deploy tendril-<env>-prompts`) without redeploying agents or foundation, and it avoids
the CFN "can't modify an exported value while it's imported" trap that a cross-stack reference
would create. Cost of resolution is one `ListPrompts` + one `GetPrompt` (cold-start today; a
TTL or per-invoke refresh can be layered on for live updates).

## Action Items

1. [x] Provision the hello prompt as `CfnPrompt` + `CfnPromptVersion`; pass `PROMPT_ID`/`PROMPT_VERSION` to the runtime; grant `bedrock:GetPrompt`.
2. [x] Agent fetches the prompt at cold start via `GetPrompt`, with a built-in default fallback.
3. [ ] Decide the caching/refresh strategy as agents multiply.
4. [ ] Introduce a dedicated prompts stack + naming convention (`<env>-<specialty>-<role>`) once there is more than one prompt.
