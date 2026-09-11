# agents/registry/

The declarative list of specialist agents deployed to AgentCore — see
[ADR-0012](../../docs/architecture/ADRs/0012-orchestrator-lambda-declarative-agent-registry.md).

One JSON file per specialist agent (mirrors the existing `guardrails/*.json` / `prompts/*.md`
policy-as-data convention — [ADR-0006](../../docs/architecture/ADRs/0006-prompt-externalization-bedrock-prompt-management.md),
[ADR-0008](../../docs/architecture/ADRs/0008-guardrails-layered-defense-in-depth.md)). Each entry
declares:

```jsonc
{
  "name": "agronomy",                 // stable id — also the AgentCore runtime name suffix
  "folder": "agronomy_agent",         // subfolder under agents/ (Dockerfile + code)
  "model_id": "...",                  // per-agent model choice (ADR-0001)
  "prompt_name": "tendril-{env}-agronomy-system",   // resolved by PromptsStack (ADR-0006)
  "guardrail_name": "tendril-{env}-agronomy-guardrail", // resolved by GuardrailsStack (ADR-0008)
  "description": "...",               // the tool description the orchestrator's model sees —
                                       // this is how it knows when to reach for this specialist
  "tools": []                         // Tool APIs (§4.2) this specialist itself needs
}
```

Two stacks read this directory and must never disagree about what's deployed:

- `AgentCoreStack` (`infra/stacks/agentcore_stack.py`) loops over every file here to provision
  one `aws_bedrockagentcore.Runtime` per entry — adding/removing a specialist is adding/removing
  a file, not editing shared stack code.
- The orchestrator (`app/orchestrator/`) builds its invocation manifest (agent name → runtime
  ARN + description) from the same files at deploy time.

Not yet populated (`hello_agent` still deploys via the old hardcoded path in
`agentcore_stack.py`) — this is a placeholder anchoring the convention ahead of the migration
tracked in ADR-0012's action items.
