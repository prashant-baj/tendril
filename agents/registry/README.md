# agents/registry/

The declarative list of specialist agents deployed to AgentCore — see
[ADR-0012](../../docs/architecture/ADRs/0012-orchestrator-lambda-declarative-agent-registry.md)
and its 2026-09-12 refinement note.

One JSON file per specialist agent (mirrors the existing `guardrails/*.json` / `prompts/*.md`
policy-as-data convention — [ADR-0006](../../docs/architecture/ADRs/0006-prompt-externalization-bedrock-prompt-management.md),
[ADR-0008](../../docs/architecture/ADRs/0008-guardrails-layered-defense-in-depth.md)). Each entry
declares:

```jsonc
{
  "name": "agronomy",                 // stable id — also the AgentCore runtime name suffix
  "template": "template_agent",       // shared codebase every entry uses by default (agents/template_agent/)
                                       // — NOT a per-agent folder. An agent is its config, not its code
                                       // (ADR-0001's factory principle). Override only for a genuinely
                                       // custom specialist; that should be the rare exception.
  "model_id": "...",                  // per-agent model choice (ADR-0001)
  "prompt_name": "tendril-{env}-agronomy-system",   // resolved by PromptsStack (ADR-0006)
  "guardrail_name": "tendril-{env}-agronomy-guardrail", // resolved by GuardrailsStack (ADR-0008)
  "description": "...",               // the tool description the orchestrator's model sees —
                                       // this is how it knows when to reach for this specialist
  "tools": ["weather"],               // names of Lambda-backed Tool APIs (§4.2, docs/stories/agent-factory.md)
                                       // this specialist may call — bound into Strands tools at cold start
  "memory": {                         // Strands Memory / context-management config (ADR-0001 Appendix A)
    "enabled": true,
    "scope": "user_garden"            // multi-tenant isolation key — see agent-factory.md AF-05
  }
}
```

Three stacks (and the orchestrator) read this directory and must never disagree about what's
deployed:

- `AgentCoreStack` (`infra/stacks/agentcore_stack.py`) loops over every file here to provision
  one `aws_bedrockagentcore.Runtime` per entry, all from the **same** `template` image —
  adding/removing a specialist is adding/removing a registry file (+ its prompt/guardrail data),
  not writing new agent code.
- `PromptsStack` / `GuardrailsStack` resolve `prompt_name` / `guardrail_name` against their own
  `prompts/*.md` / `guardrails/*.json` policy-as-data files.
- The orchestrator (`app/orchestrator/`) builds its invocation manifest (agent name → runtime
  ARN + description) from the same files at deploy time.

**Current state (2026-09-12):** `vision.json` (plant-photo identification, `google.gemma-3-27b-it`)
is the only live entry — it's a real specialist the orchestrator can call, not a stand-in.
`AgentCoreStack` loops over this directory instead of a single hardcoded `_agent_runtime()` call.
Its `template` value is `"hello_agent"` (today's actual, only agent folder) — that folder is
**pure shared-template code now**: nothing deploys it under the name "hello" anymore (the
original stand-in specialist used to prove the orchestrator pipe end-to-end before a real
specialist existed — decommissioned once `vision` took over that role). Renaming `hello_agent/`
→ `template_agent/` and repointing every entry's `template` value is still AF-02's job; the
schema doesn't change. `model_id` is left `""` on entries that want "use the
`--context model_id=...` CDK override" (unchanged deploy-time behavior); a non-empty value takes
precedence once a specialist genuinely needs a different model than the deploy-time default.
`memory` isn't set on any entry yet — that field lands with AF-05.
