# Tendril — Architecture

This document describes Tendril's **conceptual architecture** and its **engineering views**. Tendril is built in two layers — a domain-agnostic **framework** and a garden **application** built on it (see [`../project-context.md`](../project-context.md)). Key decisions are recorded as ADRs in [`./ADRs/`](./ADRs/).

---

## 1. Concept Architecture

### 1.1 Build-time: Template → Agent → Application

A single **Agent Template** (one codebase) is specialized into many agents purely by **configuration** — model, tools, prompt/skill, guardrails, memory, and context settings. Prompts and skills live outside the code in **Bedrock Prompt Management / S3**, so each specialty can be authored and versioned independently by its domain expert.

```mermaid
flowchart LR
  T["Agent Template<br/>(single codebase)"]
  CFG["Config per agent:<br/>model · tools · prompt/skill · guardrails"]
  T --> CFG
  CFG --> A1["Agronomy Agent"]
  CFG --> A2["Pest / Disease Agent"]
  CFG --> A3["Irrigation Agent"]
  CFG --> A4["Pruning Agent"]
  CFG --> A5["Beautification /<br/>Landscaping Agent"]
  CFG --> A6["... Agent N"]
  PM["Prompts &amp; Skills<br/>Bedrock Prompt Mgmt / S3"] -. injected .-> A1
  PM -.-> A2
  PM -.-> A3
  PM -.-> A4
  PM -.-> A5
  PM -.-> A6
```

The specialized agents, the model-driven orchestrator, the tool bindings, and the domain prompts are composed into an **Application** and deployed via **AWS CDK** to **dev** and **prod**.

```mermaid
flowchart TB
  subgraph APP["Application (a use case = configuration)"]
    ORCH["Model-driven Orchestrator"]
    AGENTS["Specialist Agents<br/>(from the factory)"]
    TOOLS["Tool APIs"]
    PROMPTS["Domain Prompts &amp; Skills"]
    ORCH --> AGENTS
    AGENTS --> TOOLS
    PROMPTS -. config .-> AGENTS
    PROMPTS -. config .-> ORCH
  end
  APP -->|packaged &amp; deployed| CDK["AWS CDK — dev / prod"]
  CDK --> ACORE["Bedrock AgentCore Runtimes"]
```

### 1.2 Runtime: model-driven composition

At runtime there is **no fixed execution order**. The orchestrator (Strands) decides, per request and garden context, which specialists and tools to invoke and in what sequence; specialists exchange information to reach the best course of action.

```mermaid
flowchart LR
  U["User<br/>(WhatsApp / App)"] --> API["Client API<br/>(API Gateway + Lambda)"]
  API --> ING["Garden / Ingestion Service"]
  ING --> S3[("S3<br/>media + storage")]
  ING --> ORCH["Model-driven Orchestrator<br/>(Strands on AgentCore)"]
  ORCH <--> SPEC["Specialist Agents<br/>(AgentCore runtimes)"]
  ORCH --> TOOLS["Tool APIs (MCP / REST)"]
  SPEC --> TOOLS
  TOOLS --> WX["Weather"]
  TOOLS --> VIS["Plant-ID / Vision"]
  TOOLS --> MKT["Nursery / Market"]
  ORCH --> DDB[("DynamoDB<br/>domain state + events")]
  ORCH --> MEM["Memory<br/>(Bedrock KB)"]
  SCHED["Tracker / Scheduler<br/>(EventBridge + Lambda)"] --> ORCH
  ORCH --> NOTIF["Notification Service"]
  NOTIF --> U
  GRD["Bedrock Guardrails"] -. applied .-> ORCH
  GRD -.-> SPEC
  OTEL["OpenTelemetry / X-Ray / CloudWatch"] -. observes .-> ORCH
```

---

## 2. Application Domain Model

The user's world is a small set of entities. DynamoDB is the structured source of truth (see [ADR-0002](./ADRs/0002-context-management-and-durable-state.md)).

```mermaid
erDiagram
  USER ||--o{ GARDEN : owns
  GARDEN ||--o{ PLANT : contains
  GARDEN ||--o{ GOAL : has
  PLANT ||--o{ GOAL : "may scope"
  GOAL ||--|| PLAN : "results in"
  PLAN ||--o{ TASK : "breaks into"
  TASK ||--o{ TRACKING : "tracked by"
  GOAL ||--o{ TRACKING : "progress"
  GARDEN ||--o{ MEDIA : "photos"
  PLANT ||--o{ MEDIA : "photos"
  TASK ||--o{ NOTIFICATION : "triggers"
  GARDEN ||--o{ EVENT : "logs"
  USER {
    string user_id
    string channel
    string locale
  }
  GARDEN {
    string garden_id
    string name
    string vision
    string geolocation
    string climate_zone
  }
  PLANT {
    string plant_id
    string species
    string variety
    string stage
  }
  GOAL {
    string goal_id
    string description
    string type
    string status
  }
  PLAN {
    string plan_id
    string success_criteria
    string status
  }
  TASK {
    string task_id
    string scope
    string status
    date due_date
  }
  TRACKING {
    string tracking_id
    date timestamp
    string observation
    string decision
  }
  EVENT {
    string event_id
    string type
    date timestamp
  }
```

- **Scope of a Task** is either **plant-level** or **garden-level** (part or whole garden).
- **EVENT** is the capture-first log that feeds the future data flywheel.

---

## 3. Services

| Service | Responsibility | Tech | Deploy target |
|---------|----------------|------|---------------|
| **Client API** | User-facing entry: gardens, plants, photos, goals, plan approval, status | API Gateway + Lambda | Lambda |
| **Garden / Ingestion** | Create/describe gardens; accept photos & metadata; build initial context | Lambda + S3 | Lambda |
| **Orchestrator** | Model-driven decomposition & composition of specialists/tools | Strands | AgentCore |
| **Specialist Agents** | Domain expertise (agronomy, pest, disease, irrigation, fertilizer, pruning, weather, beautification, landscaping) | Strands (factory) | AgentCore |
| **Tracker / Scheduler** | Drive the outcome loop: due follow-ups, reminders, re-evaluation | EventBridge + Lambda | Lambda |
| **Notification** | Outbound reminders & inbound replies (WhatsApp / email) | Lambda + provider | Lambda |
| **Media / Vision** | Plant-ID and photo diagnosis (specialized model or API tool) | Bedrock / external model | Tool API |
| **Memory / Knowledge** | Semantic garden history & horticultural knowledge | Bedrock Knowledge Bases | Managed |
| **Persistence** | Structured domain state + event log | DynamoDB | Managed |

---

## 4. APIs

### 4.1 Client API (user-facing)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/gardens` | POST | Create a named garden (vision, geolocation) |
| `/gardens/{id}` | GET/PATCH | Read/update garden context |
| `/gardens/{id}/plants` | POST/GET | Add / list plants (with photos) |
| `/gardens/{id}/media` | POST | Upload a plant/garden photo |
| `/gardens/{id}/goals` | POST | Submit a concern / expectation / wish |
| `/plans/{id}/approve` | POST | Human-in-the-loop plan approval |
| `/gardens/{id}/tasks` | POST/GET | Create / list plant- or garden-level tasks |
| `/gardens/{id}/status` | GET | Progress, upcoming follow-ups, history |

### 4.2 Tool APIs (agent-facing, "tools are APIs")

| Tool | Purpose | Consumer |
|------|---------|----------|
| Weather | Forecast & climate for the garden's geolocation | Orchestrator, weather/irrigation/pruning agents |
| Plant-ID / Vision | Species ID and disease/pest diagnosis from photos | Diagnosis agent |
| Nursery / Market | Availability & prices of inputs/plants | Landscaping / procurement |
| Notification | Send reminders, collect replies | Tracker, orchestrator |
| Knowledge search | Retrieve grounded horticultural references | All specialists |

All tools sit behind a uniform contract (MCP / REST) so they are discoverable and swappable.

---

## 5. Configurability

The framework's value is that behavior is **configuration**, not code.

| Configurable | Where | Examples |
|--------------|-------|----------|
| **Agent specialization** | `cdk.json` / env + Bedrock Prompt Management | model id, tool list, prompt/skill, guardrail, memory & context settings |
| **Application composition** | App config | which agents exist, tool bindings, orchestrator policy, persona/context weighting |
| **Prompts & skills** | Bedrock Prompt Management / S3 | authored & versioned per specialty by domain experts |
| **Guardrails** | Bedrock Guardrails | domain safety, content, PII policies |
| **Environment** | CDK context | `dev` / `prod` names, accounts, regions, removal policies |
| **Model per agent** | Agent config | Claude for reasoning; specialized vision model for diagnosis (see [ADR-0001](./ADRs/0001-use-strands-agents-framework.md)) |

Adding a specialty = a new prompt + a config entry. Adding a use case = a new configuration bundle; the engine is unchanged.

---

## 6. Engineering Views

### 6.1 Deployment view

```mermaid
flowchart TB
  subgraph Edge
    APIGW["API Gateway"]
    WA["WhatsApp / Email Gateway"]
  end
  subgraph Compute
    L["Lambda:<br/>ingestion · tracker · notifier"]
    AC["AgentCore:<br/>orchestrator + specialist agents"]
  end
  subgraph Data
    DDB[("DynamoDB")]
    S3[("S3")]
    KB["Bedrock Knowledge Bases"]
  end
  subgraph AI
    BR["Bedrock:<br/>models · Guardrails · Prompt Mgmt"]
  end
  subgraph Ops
    EB["EventBridge Scheduler"]
    CW["CloudWatch / X-Ray"]
  end
  APIGW --> L
  WA --> L
  EB --> L
  L --> AC
  AC --> BR
  AC --> DDB
  AC --> S3
  AC --> KB
  AC --> CW
```

### 6.2 Delivery view (GitOps + selective deploy)

Monorepo; CI detects changed folders and deploys **only** those via CDK; `main` is deployable; CI authenticates to AWS via OIDC (no static keys). Serverless-only for a NoOps posture. Details in [`../engineering-best-practices.md`](../engineering-best-practices.md).

```mermaid
flowchart LR
  DEV["Commit / PR"] --> CI["CI: lint · test · secret-scan"]
  CI --> DIFF["Path-diff:<br/>which folders changed?"]
  DIFF --> CDK["CDK deploy<br/>(changed stacks only)"]
  CDK --> ENVD["dev"]
  ENVD -->|tag / approval| ENVP["prod"]
```

### 6.3 Cross-cutting concerns

- **Security:** IAM roles (no long-lived keys), secrets in Secrets Manager/SSM, Bedrock Guardrails, least-privilege tool permissions.
- **Observability:** built-in OpenTelemetry + X-Ray traces; CloudWatch for tool latency, token/cost, and error rates.
- **Multi-tenancy:** memory/storage stores and DynamoDB keys scoped per user/garden.
- **Data & privacy:** capture-first event log with privacy-by-design (consent, PII minimization, retention/TTL).

---

## 7. Use-Case Views (dynamic workflow & state)

### 7.1 Goal intake → dynamic decomposition → plan approval

No predefined order; the model composes the path and specialists exchange information.

```mermaid
sequenceDiagram
  actor User
  participant API as Client API
  participant Orch as Orchestrator (Strands)
  participant Diag as Diagnosis / Vision
  participant Pest as Pest
  participant Agro as Agronomy
  participant Wx as Weather API
  participant State as DynamoDB
  User->>API: Submit goal + photo ("leaves curling")
  API->>Orch: goal + garden context
  Orch->>Diag: analyze photo
  Diag-->>Orch: "aphids, mild + N imbalance?"
  Note over Orch: model chooses next steps at runtime
  Orch->>Pest: organic remedy for aphids?
  Orch->>Wx: forecast for spray timing
  Pest-->>Orch: neem-oil plan
  Wx-->>Orch: dry next 3 days
  Orch->>Agro: nutrient check (yellowing)
  Agro-->>Orch: reduce nitrogen, add compost
  Note over Orch,Agro: inter-agent exchange reconciles<br/>remedy + weather + nutrition
  Orch->>State: save proposed plan + success criteria
  Orch-->>User: Propose plan (HITL confirm)
  User-->>Orch: Approve
  Orch->>State: plan = Approved; schedule follow-ups
```

### 7.2 Long-running tracking loop (pause / resume across sessions)

```mermaid
sequenceDiagram
  participant Sched as Tracker / Scheduler
  participant Orch as Orchestrator
  participant State as DynamoDB
  participant Notif as Notification
  actor User
  Sched->>Orch: wake (follow-up due, plan X)
  Orch->>State: load plan, progress, success criteria
  Orch->>Notif: "Send a photo of the new leaves"
  Notif->>User: WhatsApp reminder
  Note over Orch: session ends; compute reclaimed
  User->>Notif: replies with a photo
  Notif->>Orch: resume (interrupt response)
  Orch->>Orch: re-evaluate vs success criteria
  alt Improved
    Orch->>State: mark milestone / complete
    Orch->>Notif: "Great progress!"
  else Not improved
    Orch->>State: adapt plan
    Orch->>Notif: revised guidance
  end
```

### 7.3 Goal / Plan lifecycle (states)

```mermaid
stateDiagram-v2
  [*] --> Intake
  Intake --> Decomposing: goal understood
  Decomposing --> PlanProposed: tasks + specialists resolved
  PlanProposed --> Approved: user accepts
  PlanProposed --> Intake: user revises
  Approved --> InProgress: schedule follow-ups
  InProgress --> AwaitingUser: reminder sent
  AwaitingUser --> InProgress: data received
  InProgress --> Adapting: off-track
  Adapting --> InProgress: plan revised
  InProgress --> Completed: success criteria met
  InProgress --> Abandoned: user opts out
  Completed --> [*]
  Abandoned --> [*]
```

---

## 8. Related documents

- Concept & vision: [`../project-context.md`](../project-context.md)
- Engineering standards: [`../engineering-best-practices.md`](../engineering-best-practices.md)
- Decisions: [ADR-0001 (framework)](./ADRs/0001-use-strands-agents-framework.md), [ADR-0002 (context & state)](./ADRs/0002-context-management-and-durable-state.md)
