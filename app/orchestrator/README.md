# app/orchestrator/

The model-driven orchestrator — see [ADR-0012](../../docs/architecture/ADRs/0012-orchestrator-lambda-declarative-agent-registry.md).

- Runs as a **Lambda**, triggered asynchronously by an EventBridge event (goal submitted, a
  follow-up is due, a user reply arrived) — never inline in the Client API's request/response
  cycle.
- Uses the **Strands SDK**'s agents-as-tools pattern: each specialist agent declared in
  [`../../agents/registry/`](../../agents/registry/) is wrapped as a tool whose implementation
  calls AgentCore's `InvokeAgentRuntime` against that specialist's deployed runtime. The
  invocation manifest (agent name → runtime ARN + tool description) is built from the registry
  at deploy time and passed in via environment variables — the orchestrator never discovers
  agents at runtime (e.g. via `ListAgentRuntimes`); *which* specialists exist is declarative,
  *which* the model calls and in what order is not.
- Persists plan/progress state to DynamoDB and pushes results over the WebSocket API
  (ADR-0004); the long-running, multi-day tracking loop lives in that state + EventBridge wake
  events, not in a single live invocation (Lambda's 15-minute ceiling only bounds one turn).

Not yet scaffolded — this is a placeholder anchoring the convention (see
[ADR-0012](../../docs/architecture/ADRs/0012-orchestrator-lambda-declarative-agent-registry.md)'s
action items) ahead of implementation.
