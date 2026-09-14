# ADR-0009: AWS Agent Toolkit as local developer tooling

**Status:** Accepted
**Date:** 2026-09-11
**Deciders:** Project owner / lead engineer

## Context

Developers on Tendril use AI coding agents (Claude Code, and potentially Cline / Gemini CLI)
against real AWS accounts for exploration, debugging, and day-to-day AWS interaction (checking
stack status, inspecting resources, reading docs/skills). AWS publishes an official
[Agent Toolkit](https://github.com/aws/agent-toolkit-for-aws) that:

- installs/authenticates the AWS CLI v2 via browser-based `aws login` (no static keys),
- installs a curated set of AWS "skills" (agent-readable guidance documents) for each detected
  AI tool, and
- registers a local `aws-mcp` MCP server (`mcp-proxy-for-aws`, run via `uvx`) that proxies MCP
  calls to `https://aws-mcp.<region>.api.aws/mcp` under a named AWS CLI profile.

This was set up on a developer workstation (`prashant.baj`) by following AWS's published
`setup-instructions/setup.md`, using AWS CLI profile `tendril` (the pre-existing `default`
profile already held access-key credentials and was left untouched). Per CLAUDE.md, this is
architecturally significant — it introduces a new external dependency (the `aws-mcp` MCP
server / Agent Toolkit) and edits the project's own governance file (`CLAUDE.md`) — so it
requires an ADR. This ADR is written to backfill that requirement; the setup itself was already
approved and run at the user's explicit request before the ADR was drafted, on the understanding
it would be documented immediately after.

## Decision

Adopt the **AWS Agent Toolkit as local, developer-machine-only tooling** — not as a runtime or
deployed dependency of Tendril.

- Scope: `~/.claude.json` (Claude Code), `~/.cline/mcp.json`, `~/.gemini/settings.json` — i.e.
  **each developer's personal AI-tool configuration**, outside the Tendril repo and outside any
  deployed stack (AgentCore, CDK, Lambda, etc.).
- The `aws-mcp` entry in each config was pinned to the `tendril` CLI profile via
  `"env": {"AWS_MCP_PROXY_PROFILES": "tendril"}` (the Agent Toolkit's generated entry falls back
  to `default` otherwise, which would silently pick up whatever credentials live there).
- `CLAUDE.md` was appended with AWS's "advanced experience" agent-rules block, wrapped in
  `<!-- BEGIN/END AWS Agent Toolkit rules -->` markers so re-running the AWS setup is idempotent
  and the rest of CLAUDE.md is never overwritten. Per that block's own first line, and per
  CLAUDE.md's existing precedence, **Tendril's own instructions win on any conflict.**
- Nothing in `agents/`, `infra/` (CDK), or any deployable folder was touched. No production
  credentials, IAM roles, or CDK context were changed. No AWS account IDs/ARNs were hardcoded
  anywhere in the repo (satisfies the existing hard rule in CLAUDE.md).

## Options Considered

| Option | Verdict |
|--------|---------|
| **A. Local dev-tooling only (this ADR)** | **Chosen** — gives developers AWS docs/skills and a sandboxed MCP proxy for exploration without touching runtime architecture |
| B. Decline / do not install | Rejected — the toolkit is genuinely useful for AWS exploration during a fast-moving hackathon build, and the risk is well-contained to a dev machine |
| C. Wire `aws-mcp` into the deployed agent runtime (AgentCore) as a live tool | Rejected here — that would be a real runtime/tool-boundary change requiring its own ADR (tool scoping, IAM least-privilege, guardrail coverage per ADR-0008); out of scope for this decision |

## Trade-off Analysis

The toolkit's browser-based `aws login` avoids the worse alternative (long-lived static access
keys pasted into a dev machine), and confining it to per-developer AI-tool config (not the repo)
keeps it outside anything CI/CD or the deployed stack depends on. The cost is a new local
dependency (`uvx`/`mcp-proxy-for-aws`) each developer must trust, and a CLAUDE.md edit that
future readers need to know is AWS-authored, not Tendril-authored — mitigated by the explicit
marker block and the precedence statement.

## Consequences

- **Easier:** developers get AWS skills/docs surfaced directly in their AI tool, and a
  browser-authenticated (no static keys) MCP path for ad hoc AWS queries during development.
- **Harder:** one more per-developer setup step to keep in sync if AWS revises the toolkit; the
  `aws-mcp` proxy's `AWS_MCP_PROXY_PROFILES` binding is per-machine config, not committed
  anywhere, so it must be redone on a new workstation or by a new contributor.
- **Security note:** the AWS CLI profile used for this setup ended up authenticated as the
  account **root** user. That's outside Tendril's control (it reflects how the AWS account is
  set up, not the toolkit), but it's called out here because CLAUDE.md's hard rules say
  "IAM roles, not access keys" — root usage for day-to-day dev work should be replaced with an
  IAM user/role with least-privilege access as a follow-up.
- **To revisit:** if the team later wants `aws-mcp` available to the *deployed* agent (not just
  developer workstations), that is a separate, materially different decision — new tool-boundary,
  new IAM scoping, new guardrail coverage — and needs its own ADR rather than extending this one.

## Action Items

1. [x] Run AWS's `setup-instructions/setup.md` (AWS CLI v2 already present; browser `aws login`
   under profile `tendril`; `aws configure agent-toolkit`; skills + `aws-mcp` MCP server
   installed for Claude Code, Cline, Gemini CLI).
2. [x] Pin `AWS_MCP_PROXY_PROFILES=tendril` on every generated `aws-mcp` MCP server entry.
3. [x] Append AWS's advanced-experience rules to `CLAUDE.md` under idempotent markers, with
   Tendril's own instructions kept as the tie-breaker.
4. [x] Backfill this ADR.
5. [ ] Follow up: move off root credentials for the `tendril` AWS CLI profile to a
   least-privilege IAM role/user.
6. [ ] If any future work wants the deployed agent (not just developer tooling) to call
   `aws-mcp`, open a new ADR — do not extend this one's scope.
