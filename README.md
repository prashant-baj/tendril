# 🌱 Tendril

> A goal-driven multi-agent assistant that **composes its own workflow at runtime** — it diagnoses your plants, plans their care, and keeps following up until they actually thrive. Built on **AWS Strands Agents** & **Amazon Bedrock AgentCore**.

---

## What it is

Tendril is two things at once:

1. **A working organic-gardening agent.** Snap a photo of a plant, ask a question ("why are the leaves yellowing?", "is this ready to harvest?"), and Tendril figures out what's wrong, builds a care plan, and *stays with you* — sending reminders and asking for follow-up photos over days and weeks — until the goal (a healthy, thriving plant) is reached.

2. **A domain-agnostic multi-agent engine underneath it.** Tendril is built on a reusable engine: swap the prompts, skills, and tool bindings and the same framework powers an entirely different application — the framework itself doesn't change.

## Why it's different

Most plant apps are static: they identify a plant, look up a generic care template, and fire fixed-interval reminders. Tendril doesn't run a predefined schedule or a hard-coded pipeline. Its orchestrator is **model-driven** — the combination and sequencing of specialist agents and tools is decided *at runtime*, based on what the photo shows, what you ask, and what you report back. And it's **outcome-oriented**: it owns a goal and closes the loop, rather than answering once and stopping.

## Example: from photo to a thriving plant

```
Day 1  You upload a photo of a chili plant + "leaves are curling."
       → Orchestrator calls: vision/diagnosis → pest specialist → organic-remedy
         specialist → weather API. Diagnosis: aphids. It proposes a neem-oil
         spray plan and a 14-day recovery goal (you tap to confirm).

Day 3  Tracker agent (scheduled) checks in via WhatsApp: "How do the new
       leaves look? Send a photo." You reply with a photo.

Day 5  New photo shows no improvement → orchestrator re-reasons, escalates:
       revises the plan, checks humidity forecast, adjusts spray timing.

Day 12 Follow-up photo shows healthy new growth → goal met. Tracker closes
       the loop and logs the outcome.
```

No two runs take the same path — the workflow is *composed*, not selected from a fixed graph.

## How it works

- **Tools are APIs.** Weather, market prices, plant-ID/vision, notifications (WhatsApp/email) — each exposed behind a uniform contract the agents can discover and call.
- **Agents come from a factory.** One agent template, specialized purely by configuration (model, tools, and a prompt/skill loaded from S3 / Bedrock Prompt Management). Adding a specialist = a new prompt + a config entry, not a new codebase.
- **A model-driven orchestrator** dynamically selects and sequences the specialists per request and per user context (urban gardener vs. farmer).
- **An outcome loop** persists the plan, success criteria, and progress (DynamoDB / AgentCore Memory), and a scheduled tracker agent drives follow-ups until the goal is reached.
- **Deterministic plumbing, model-driven brain** — S3 ingestion, scheduling, persistence, and notifications are deterministic; the intelligence lives in the model-driven orchestration.

## Architecture

_High-level architecture, domain model, services, APIs, and sequence diagrams: see [`docs/architecture/architecture.md`](docs/architecture/architecture.md)._

## Built with

- [AWS Strands Agents SDK](https://strandsagents.com/) — model-driven agent loop & multi-agent patterns
- Amazon Bedrock AgentCore — runtime, sessions, memory, observability
- Amazon Bedrock (Anthropic Claude) — reasoning & vision
- Amazon S3 / Bedrock Prompt Management — externalized prompts & skills
- Amazon DynamoDB — outcome-loop state
- Amazon EventBridge — scheduled follow-ups
- OpenTelemetry / AWS X-Ray — tracing & observability
- AWS CDK — infrastructure as code

## Repo structure (planned)

```
tendril/
├── docs/          # vision, architecture, user stories, ADRs
├── agents/        # agent template + specialist agents (factory)
├── app/           # orchestrator, ingestion, tracker, tools
├── infra/         # AWS CDK stacks
└── prompts/       # externalized skills/prompts (the "application")
```

## Documentation

- [`docs/project-context.md`](docs/project-context.md) — concept, vision, personas, and the "why Tendril" narrative
- [`docs/architecture/architecture.md`](docs/architecture/architecture.md) — architecture, domain model, services, APIs, and diagrams
- [`docs/architecture/ADRs/`](docs/architecture/ADRs/) — architecture decision records
- [`docs/engineering-best-practices.md`](docs/engineering-best-practices.md) — engineering standards checklist
- `docs/stories.md` — user stories / build backlog (planned)

## License

Released under the [MIT License](LICENSE).
