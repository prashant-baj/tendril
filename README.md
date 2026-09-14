# 🌱 Tendril

> A goal-driven multi-agent assistant that **composes its own workflow at runtime** — it diagnoses your plants, plans their care, and keeps following up until they actually thrive. Built on **AWS Strands Agents** & **Amazon Bedrock AgentCore**.

---

## What it is

Tendril is two things at once:

1. **A working organic-gardening agent.** Snap a photo of a plant, ask a question ("why are the leaves yellowing?", "is this ready to harvest?"), and Tendril figures out what's wrong, builds a care plan, and *stays with you* — proactively checking back in and asking for follow-up photos over days and weeks — until the goal (a healthy, thriving plant) is reached.

2. **A domain-agnostic multi-agent engine underneath it.** Tendril is built on a reusable engine: swap the prompts, skills, and tool bindings and the same framework powers an entirely different application — the framework itself doesn't change.

## Why it's different

Most plant apps are static: they identify a plant, look up a generic care template, and fire fixed-interval reminders. Tendril doesn't run a predefined schedule or a hard-coded pipeline. Its orchestrator is **model-driven** — the combination and sequencing of specialist agents and tools is decided *at runtime*, based on what the photo shows, what you ask, and what you report back. And it's **outcome-oriented**: it owns a goal and closes the loop, rather than answering once and stopping.

## Example: from photo to a thriving plant

```
Day 1  You submit a photo of a curry leaf plant + "looks sparse and stressed."
       → Orchestrator calls the vision specialist, which diagnoses likely
         overwatering/root stress from the photo alone — no fixed pipeline of
         specialists runs; the model decides who else (if anyone) to consult
         per issue. It proposes a 4-task plan ("Done when: leaves perk up and
         stems stop drooping within 2 weeks") and you approve it.

Day 4  A scheduled Tracker Lambda finds the task overdue and nudges — right
       inside the same chat thread you started on Day 1: "Just checking in —
       how's 'Check soil moisture' going?" Next time you open the app, it's
       there waiting.

Day 4  You check in with a new photo. The orchestrator resumes that exact
       session (not a new conversation), looks at the new photo, and reports
       back honestly: "still needs a bit more time... hold off on watering
       and focus on improving drainage."

Day 18 The Activity tab shows the whole arc — issue, plan, nudge, check-in —
       as one continuous, timestamped thread.
```

No two runs take the same path — the workflow is *composed*, not selected from a fixed graph. And
today, every follow-up happens **in-app** (the gardener sees it next time they open Tendril) —
there's no push notification channel (WhatsApp/SMS/web-push) wired up yet; that's a real,
tracked gap, not a design choice (see [ADR-0004](docs/architecture/ADRs/0004-backend-api-serverless-storage.md)).

## How it works

- **Tools are APIs.** Today that's a real Weather tool (a Lambda behind an IAM-authenticated Function URL); the pattern is built to add more (market prices, notifications, IoT actuators) behind the same uniform contract without touching agent code.
- **Agents come from a factory.** One shared agent codebase (`agents/hello_agent/`), specialized purely by a declarative registry entry (`agents/registry/*.json`: model, tools, prompt, guardrail, per-garden memory). Adding a specialist = a new registry entry, not a new codebase — 5 specialists exist today (vision, agronomy, irrigation, pest/disease, pruning).
- **A model-driven orchestrator** (Strands, on Lambda) dynamically decides which of the registered specialists to consult per issue, and in what order — never a fixed pipeline.
- **An outcome loop** persists the goal, plan, tasks, and event history in DynamoDB, gives each specialist a real per-garden memory via a Bedrock Knowledge Base, and a scheduled Tracker Lambda finds overdue tasks and nudges — right into the same chat thread — until the goal is reached.
- **Deterministic plumbing, model-driven brain** — ingestion, scheduling, and persistence are plain code; the reasoning lives entirely in the model-driven orchestration.

## Architecture

_High-level architecture, domain model, services, APIs, and sequence diagrams: see [`docs/architecture/architecture.md`](docs/architecture/architecture.md)._

## Built with

- [AWS Strands Agents SDK](https://strandsagents.com/) — model-driven agent loop, agents-as-tools orchestration, session persistence
- Amazon Bedrock AgentCore — one runtime per specialist agent, provisioned by CDK from a declarative registry
- Amazon Bedrock — foundation models (reasoning + vision), Guardrails (one per specialist), Prompt Management (externalized prompts)
- Amazon Bedrock Knowledge Bases (S3 Vectors) — real per-garden memory for every specialist
- Amazon API Gateway + AWS Lambda (container images) — the Client API, orchestrator, and Tracker
- Amazon DynamoDB — single-table domain state + event log
- Amazon EventBridge (Rules + Scheduler) — async goal/check-in routing and the Tracker's follow-up polling
- Amazon S3 — media storage and the frontend's static hosting
- Angular PWA — the web frontend
- AWS CDK — infrastructure as code, one stack per concern
- AWS X-Ray — available on every AgentCore runtime, opt-in via CDK context (off by default in dev)

## Repo structure

```
tendril/
├── docs/          # vision, architecture, user stories, ADRs
├── agents/        # shared agent template (factory) + declarative agent registry
├── app/           # Client API, orchestrator, tracker, tools (weather)
├── frontend/      # Angular PWA
├── infra/         # AWS CDK stacks (one per concern)
├── prompts/       # externalized prompts (Bedrock Prompt Management source)
├── guardrails/    # externalized guardrail policies (Bedrock Guardrails source)
├── scripts/       # local dev/eval scripts (e.g. guardrail evaluation)
└── tests/         # cross-cutting tests (most live alongside their own package)
```

## Documentation

- [`docs/project-context.md`](docs/project-context.md) — concept, vision, personas, and the "why Tendril" narrative
- [`docs/architecture/architecture.md`](docs/architecture/architecture.md) — architecture, domain model, services, APIs, and diagrams
- [`docs/architecture/ADRs/`](docs/architecture/ADRs/) — architecture decision records
- [`docs/engineering-best-practices.md`](docs/engineering-best-practices.md) — engineering standards checklist
- [`docs/stories/`](docs/stories/) — user stories / build backlog

## License

Released under the [MIT License](LICENSE).
