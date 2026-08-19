# Tendril — Project Context: Concept & Vision

> This document is the single source of truth for *what* Tendril is and *why* it exists. It is written to brief both people and AI coding assistants — keep it current, and feed it as context to any assistant working on the codebase.

---

## 1. Purpose

Tendril helps **amateur gardeners** grow and maintain **flower gardens and vegetable (kitchen) gardens** by handholding them through every task needed to get the best out of their garden — from soil prep to harvest, from a single struggling plant to a full seasonal landscaping vision.

It is delivered as a **reference implementation** of a broader idea: a configurable, model-driven multi-agent engine on which many such domain applications can be built.

## 2. Vision & Mission

**Vision:** Every hobby gardener has an expert horticulturist in their pocket — one that understands *their* specific garden, *their* climate, and *their* goals, and stays with them until those goals are met.

**Mission:** Turn a vague wish ("I want my roses to bloom for the festival", "why won't my tomatoes fruit?") into an adaptive, tracked plan executed by a team of specialist AI agents that reason together, follow up over days and weeks, and remember everything.

## 3. The Two Parts

Tendril is deliberately built in two layers. The framework is domain-agnostic; the application is the garden use case that proves it.

### Part 1 — Configurable Agent Framework (the engine)

A reusable, use-case-agnostic foundation built on AWS:

- **Amazon Bedrock — foundation models:** reasoning and vision (Anthropic Claude), swappable per agent.
- **Amazon Bedrock Guardrails:** safety, content, and domain guardrails applied consistently across agents.
- **Amazon Bedrock Prompt Management:** versioned prompts and skills — externalized, not hardcoded.
- **Amazon Bedrock AgentCore:** runtime hosting, isolated sessions, memory, and observability for each agent.
- **Strands Agents SDK:** the model-driven agent loop and multi-agent patterns (agents-as-tools, Swarm, Graph) that let the model — not a hardcoded flow — decide which agents and tools to use.

Framework principles:
- **Tools are APIs** — every capability sits behind a uniform, discoverable contract.
- **Agents come from a factory** — one template, specialized purely by configuration (model + tools + prompt/skill).
- **Model-driven orchestration** — no fixed execution order; the workflow is composed at runtime.
- **Configuration is the application** — prompts, skills, tool bindings, and pipeline config define a use case without changing the engine.

### Part 2 — The Application (Garden reference implementation)

The garden use case built *on* the framework:

- **Domain microservices & APIs** — weather, geolocation, plant identification / vision, notifications (email / WhatsApp), nursery / market lookups, and a garden knowledge base.
- **Workflow / Strands pipeline** — the model-driven orchestrator plus the specialist gardening agents.
- **Actual prompts & skills** — the horticultural expertise, expressed as configuration.
- **Deployment** — the entire agentic application is deployed via **AWS CDK** (monorepo, per-service, environment-parameterized: dev & prod).

## 4. Target Users & Personas

Tendril is for **amateurs** — people with enthusiasm and a garden, but not expert knowledge, who want to be guided rather than handed a generic care sheet.

**Persona A — Meera, the busy balcony grower (kitchen garden).**
34, works full-time, lives in a Pune apartment with a sunny balcony and a few window boxes. Grows herbs, tomatoes, chilies, and spinach for fresh, organic food. Loves the idea but keeps losing plants to over/under-watering and doesn't know why things fail. Wants specific, "for *my* balcony" guidance and gentle reminders — not a forum thread.

**Persona B — Rajesh, the retired flower enthusiast (ornamental garden).**
58, retired, has time and a backyard rose-and-marigold bed he's proud of. Wants show-quality blooms timed to festivals and family occasions, plus ideas to beautify a bare corner. Ready to put in the work; needs expert-level scheduling, diagnosis, and landscaping inspiration.

**Persona C — The Sharma family, first-timers (mixed terrace garden).**
A couple with two kids starting a terrace garden of mixed vegetables and flowers as a family activity. Overwhelmed by conflicting advice online. Want a simple, guided, encouraging experience that tells them what to do next and celebrates progress.

## 5. Core Concept & Domain Model

The user's world is organized around a few simple entities:

- **Garden (project):** a named project the user creates (e.g., "Balcony Kitchen Garden", "Front Rose Bed") with a described **vision**, a **geolocation**, and its **plants** (with photos, varieties, ages).
- **Plant:** an individual plant or bed within a garden, with its own photos, history, and tasks.
- **Goal / Concern / Wish:** anything the user wants — a problem ("leaves yellowing"), an objective ("maximize blooms by Oct 20"), or a wish ("make this corner beautiful").
- **Plan:** the AI-generated, user-approved set of tasks that will achieve a goal.
- **Task:** a unit of work, at **plant level** or **garden level** (part or whole garden). Tasks can be user-created or agent-proposed.
- **Tracking record:** progress, follow-ups, reminders, and captured data (photos, replies) against a plan until completion.
- **Context & history:** everything is remembered — the garden's full timeline, past problems, what worked, and seasonal patterns.

## 6. How It Works (end-to-end)

1. **Create a Garden.** The user creates a named garden, describes their vision, drops a geolocation, and adds plants with photos.
2. **Build initial context.** The AI processes the description, photos, location (climate, season, sunlight), and plant list to build a working context for that specific garden.
3. **Clarify.** The AI asks targeted questions about the user's specific expectations and problems ("How many hours of direct sun does the balcony get?", "What's your goal — food, looks, or both?").
4. **Intake a goal.** The user explains a concern, expectation, or wish in plain language.
5. **Decompose dynamically.** The Strands pipeline identifies the tasks required to meet that purpose and determines **which specialist agents and tools are needed** — *there is no pre-defined execution order*. It is decided at runtime from the goal, the garden context, and what the photos reveal.
6. **Agents reason together.** Specialists exchange information to reach the best course of action — e.g., the **pruning** decision depends on the **weather** forecast and the **desired bloom date**; a **fertilizer** recommendation depends on **agronomy** (soil), the **crop stage**, and any **disease** finding.
7. **Propose a plan.** The system presents a concrete plan (tasks, timing, rationale). Nothing irreversible happens without user acceptance.
8. **Track to completion.** Once accepted, Tendril tracks progress with **follow-ups and reminders** (email / WhatsApp), asks for **follow-up data** (a new photo, an observation), re-evaluates against the goal, adapts the plan when reality differs, and continues **until the goal is achieved**.
9. **Remember.** All history and context is retained, so later goals build on everything learned about this garden.

**Time horizon:** a task may run from **days to weeks to a full harvest season**. Tendril is built for long-running, outcome-oriented engagement, not one-shot answers.

## 7. Specialist Agents & Tools

The specialist agents are configuration on the same template; the orchestrator draws on whichever the goal requires.

**Specialist agents (illustrative, extensible):**
- **Agronomy / soil preparation** — soil health, amendments, planting medium.
- **Pest control** — identification and organic-first treatment of pests.
- **Disease** — diagnosis and management of plant diseases.
- **Irrigation / watering** — schedules tuned to plant, pot/bed, season, and weather.
- **Fertilizer / nutrition** — feeding plans by crop stage and goal (e.g., bloom vs. growth).
- **Pruning** — what, when, and how, timed to the outcome.
- **Weather impact** — heat, frost, rain, and humidity effects and mitigations.
- **Beautification** — aesthetics, color schemes, arrangement ideas.
- **Landscaping / more planting** — companion planting, new-plant and layout ideas.

**Tools (APIs):**
- Weather & climate, geolocation, plant identification / vision (photo diagnosis), notifications (email / WhatsApp), nursery / market lookups, and a horticultural knowledge base.

## 8. Example Walkthroughs

### 8a. Kitchen garden — Meera's tomatoes won't fruit

Meera creates **"Balcony Kitchen Garden"** (Pune geolocation), adds her tomato, chili, and spinach plants with photos, and writes her vision: *"fresh organic veggies from my balcony."* Tendril builds context (hot climate, ~5 hrs sun, containers) and asks about pot size and watering. Meera reports: *"tomatoes are flowering but no fruit, and lower leaves are yellowing."*

The pipeline composes a path — no fixed order:
- **Vision/diagnosis** reads the photos (healthy flowers, yellow lower leaves).
- **Agronomy** flags likely excess nitrogen / nutrient imbalance for fruiting stage.
- **Pollination** advises hand-pollination — balconies lack bees.
- **Weather** notes a heat spell stressing fruit set.
- **Irrigation** adjusts watering to reduce stress.

**Plan (3-week goal, fruit set):** switch to a bloom/fruit fertilizer, hand-pollinate each morning, mulch pots for heat, adjust watering. Meera accepts. Tendril reminds her to hand-pollinate, requests weekly photos, and — when week 2 shows still no set — re-reasons and nudges timing/technique. Goal closes when fruits appear.

### 8b. Flower garden — Rajesh wants blooms for a festival + a beautified corner

Rajesh creates **"Front Rose Bed"**, adds photos and location, and states two goals: *"maximum blooms by the festival in 6 weeks"* and *"make the bare left corner beautiful."*

The pipeline pulls in a different set of specialists, reasoning together:
- **Pruning** determines a bloom-flush prune, but its **timing negotiates with Weather** (avoid a forecast heatwave) and the **6-week bloom target**.
- **Agronomy + Fertilizer** set a feeding schedule aligned to the prune date.
- **Disease** spots early black-spot in a photo; **Pest** checks for aphids — both fold treatments into the schedule.
- **Beautification + Landscaping** propose companion plants, a trellis, and a color scheme for the bare corner.

**Plan (6-week, staged):** prune this weekend, feed on a schedule, treat black spot, plant the corner with recommended companions. Rajesh accepts. Tendril tracks each stage with reminders and photo check-ins, adjusting if disease returns or weather shifts, right up to the festival.

## 9. Key Principles & Differentiators

- **Model-driven, not scripted:** the combination and sequencing of agents/tools is decided at runtime — unlike fixed-schedule plant apps.
- **Outcome-oriented:** owns a goal and closes the loop with follow-ups until it's achieved.
- **Long-horizon memory:** full garden history and context persist across days, weeks, and seasons.
- **Handholding for amateurs:** guidance is specific to *this* garden and user, and encourages progress.
- **Organic-first:** favors natural, chemical-free approaches for food and family gardens.
- **Built on a reusable engine:** the garden app is one instantiation of a domain-agnostic framework.
- **Right model for the job:** model-agnostic agents can use specialized models (e.g., a dedicated plant-disease vision model) wherever they beat a general model — not locked to a single foundation model.

## 10. Why Tendril over General-Purpose AI Assistants

Tendril runs on the same frontier models as today's best assistants (Anthropic Claude, via Amazon Bedrock). The difference is **not** raw intelligence — it's everything built around the model. General-purpose assistants and copilots — ChatGPT, Claude (including Cowork), Google Gemini, Microsoft 365 Copilot, GitHub Copilot, Perplexity — are extraordinarily capable, but they are **general, single-user, session-scoped, and user-driven**: they wait for a capable human to ask, respond once, and largely forget. Tendril is a **purpose-built agent** for a specific job and a specific, underserved user.

### Assistant vs. Agent — the core distinction

A general assistant needs a capable human continuously *driving* it: you must know what to ask, supply context each time, judge the answer, and remember to follow up. Tendril **owns the outcome**. You state a wish ("get my roses blooming for the festival"); it decomposes the work, runs a team of specialists, proposes a plan, and then *drives itself* — following up, collecting data, adapting — until the goal is met. That shift from *a tool you operate* to *an agent that pursues your goal* is the entire point.

**Concrete contrast:** Ask a general assistant "why are my tomato leaves yellowing?" and you get a solid, generic answer — once. Tendril knows it's *your* Pune balcony, that these are the same plants you asked about three weeks ago, checks whether the last fix actually worked, and adapts the plan if it didn't.

### What general assistants can't readily do

| Dimension | General assistant / copilot | Tendril |
|---|---|---|
| Interaction | You drive; it responds and forgets | Owns a goal and drives itself to completion |
| Intended user | Someone comfortable operating an AI tool | Amateur gardeners, via WhatsApp, with zero AI literacy |
| Expertise | As good as your prompt; general knowledge | Curated, grounded, expert-authored per specialty |
| Memory | Session / project scoped | Persistent garden context across days, weeks, seasons |
| Proactivity | Reactive unless you hand-script reminders | Proactive follow-ups and reminders until done |
| Structure | One general model | Model-driven orchestra of specialist agents |
| Delivery | An app/console you must open and operate | Comes to the user on their own channel |
| Data | Siloed to one user's session | Structured outcomes captured by design (future flywheel) |
| Reuse | A product you use | A reusable framework experts configure per domain |

### The moats that make it a project, not a prompt

1. **Expert-configured agents (separation of knowledge).** No single person can author authoritative guidance across floriculture, entomology, soil agronomy, and landscaping. Tendril's factory + externalized-prompt architecture lets each domain expert own and version their agent's knowledge independently (via Bedrock Prompt Management), grounded in vetted sources. A single assistant session starts from zero every time; Tendril ships with a maturing, curated expert knowledge base — and improves it over time.

2. **A data flywheel — by design, for the future.** Every tracked outcome (problem → plan → result, by plant, variety, climate, and season) is captured as structured data. *Aggregating and learning from that data across thousands of users is a separate future intervention* — but the architecture is **capture-first and privacy-by-design from day one**, so the compounding asset accrues rather than being lost. General assistants are structurally siloed and cannot accumulate a shared, cross-user outcome corpus.

3. **Right model for each job — not locked to one.** Agents are model-agnostic: Strands makes the model a one-line, per-agent choice, and Bedrock offers many providers, so each specialist can use the best-fit model — a strong reasoning model (Claude) for planning, a specialized or fine-tuned computer-vision model for plant-disease diagnosis, a small cheap model for routing/classification, an embeddings model for retrieval. Specialized models that aren't on Bedrock can be exposed as API tools (e.g., a dedicated plant-disease endpoint). General assistants are effectively locked to a single vendor's model family; Tendril picks the right model — and the right cost/latency profile — per task, and can adopt better specialized models as they emerge without re-architecting. *(Scope note: this reference implementation runs on a single foundation model; configuring third-party or custom — fine-tuned or self-hosted — models per agent is an architectural capability, not a current dependency.)*

### The honest bottom line

The intelligence is shared; the **system around it** is the product. Tendril delivers expert, proactive, goal-completing help to people who would never open — let alone operate — a general AI assistant, with curated per-specialty expertise, long-horizon memory, and a data foundation built to compound. That is the line between an *assistant* and an *agent that does real work for real people*.

## 11. Non-Goals (for now)

- Not a marketplace or e-commerce platform (it may *recommend* inputs, not run a store).
- Not a replacement for professional agronomy on commercial farms (amateur home gardens are the focus).
- Not a social network — the relationship is between the user and their garden agent.

## 12. Glossary

- **Garden** — a user's named project (a space they tend).
- **Goal / Concern / Wish** — anything the user wants addressed.
- **Plan** — the approved set of tasks to reach a goal.
- **Specialist agent** — a configured expert (agronomy, pest, pruning, …).
- **Tracker** — the component that follows up and drives a plan to completion.
- **Framework** — the domain-agnostic engine (Part 1).
- **Application** — the garden use case built on the framework (Part 2).
