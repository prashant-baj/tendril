# ADR-0015: IoT-Driven Autonomous Actuation & Strands-Native Human-in-the-Loop (Future State)

**Status:** Proposed
**Date:** 2026-09-14
**Deciders:** Project owner / lead engineer

## Context

Every specialist Tendril has today (Vision, Agronomy, Irrigation, Pest/Disease, Pruning) is
**advisory and read-only** — a tool call to one of them, or to the Weather Tool, never has a
real-world side effect. This was evaluated directly against Strands' actual native
human-in-the-loop mechanism (`strands.vended_interventions.HumanInTheLoop`, verified against the
installed `strands-agents==1.55.1` SDK): its `Confirm`/interrupt primitive is explicitly scoped to
**gating a tool call** — the dataclass docstring states plainly that it is "only supported on
`beforeToolCall`." There is currently nothing in Tendril worth gating that way: today's
plan-approval flow (PA-02) isn't a tool call at all (the `updated_plan` comes from a separate,
prompt-less `structured_output_model` call, not from the model invoking a tool), and gating the
read-only specialist consultations would only add friction — the gardener would have to approve
each specialist call mid-turn, breaking the seamless "submit an issue, get a diagnosis" experience
this app is built around.

That changes the moment this system is deployed against **real IoT-connected hardware** in a
garden, farm, or field — not a hypothetical, but the natural next step for an agentic system that
already reasons about soil, water, and plant health. Once sensors feed live telemetry and the
orchestrator (or a dedicated field-ops agent) gains **actuator tools** — open an irrigation valve,
run a mister, vent a greenhouse, dose fertilizer, dispatch a ground robot, task a drone — those
tool calls have real, sometimes irreversible, physical and financial consequences. Some of them
are perfectly safe to run autonomously (routine watering within a normal envelope); some are
genuinely critical and deserve a human's explicit sign-off first (a large dose during a storm
warning, any chemical application, any drone flight outside an approved zone). This is precisely
the shape Strands designed `Confirm`/interrupt for — a real tool call, about to have a real
effect, that a human should be able to stop or approve before it executes.

This ADR records that future-state design now, while the mismatch with today's advisory-only
system is fresh, so the decision is ready the moment real hardware integration is actually
scoped — not to imply that work is starting now. **No code changes accompany this ADR.**

## Decision

When Tendril grows IoT-driven sensing and autonomous actuation (fixed equipment, ground robots,
and drones), adopt Strands' native `HumanInTheLoop` intervention to gate **critical** actuator
tool calls only. Routine, low-risk actuator calls are allow-listed
(`HumanInTheLoop(allowed_tools=[...])`) to run autonomously — the same declarative, per-tool
scoping posture `agents/registry/*.json`'s `tools: [...]` lists already use today (AF-03) to
decide which tools a specialist may call at all. Criticality starts as a static per-tool
classification (same declarative style) and may later adopt the SDK's built-in LLM risk
`classifier=True` if a static allow/deny split proves too coarse for real field conditions.

## Architecture Sketch

### Ingestion — reuses the existing EventBridge-first pattern, not a new paradigm

**AWS IoT Core** (device connectivity/MQTT) → **IoT Rules Engine** → **Amazon EventBridge** —
deliberately the same asynchronous, event-driven path every existing trigger in this repo already
uses (`goal.submitted`, `followup.due`, etc.). Covers fixed IoT (soil-moisture probes, weather
stations, greenhouse climate sensors) and mobile platforms (drones, ground robots) reporting
telemetry, position, and battery state over the same MQTT path.

### New entities (sketch only — not implemented; would extend `data-architecture.md` §2)

| Entity | `pk` | `sk` | Notes |
|---|---|---|---|
| Device | `GARDEN#{garden_id}` | `DEVICE#{device_id}` | `device_type`: `sensor` \| `drone` \| `robot` \| `actuator` |
| SensorReading | `GARDEN#{garden_id}` | `READING#{device_id}#{iso_timestamp}` | Time-sortable, mirrors the `Event` entity's existing `EVENT#{iso_timestamp}#{id}` trick |
| ActuatorAction | `GARDEN#{garden_id}` | `ACTION#{iso_timestamp}#{action_id}` | Audit trail: what was proposed, auto-approved-by-allowlist vs. required-a-human, who approved, when, outcome |

New event `sensor.threshold_crossed` (or per-metric variants) routes to the orchestrator exactly
like `followup.due` does today (Phase 7.5+) — the same one-Rule-per-detail-type pattern already
established in `infra/stacks/agentcore_stack.py`.

### Extended use cases — three tool categories, by physical actuation type

Each a new `app/tools/<name>/` folder — the exact same shape as today's Weather Tool
(`app/tools/weather/`: Lambda + IAM-authenticated Function URL, bound to a specialist purely via
its own registry entry's `tools: [...]` list) — AF-03's existing pattern reused verbatim, not a
new one:

- **Fixed ground actuators** — irrigation valves, misting systems, greenhouse vents, grow lights,
  fertilizer/nutrient dosers.
- **Ground robots** — autonomous weeding, targeted spot-treatment, harvest-assist, and mobile
  scouting (drive to a zone, report soil/plant condition). A tool call here dispatches a command
  to the robot's own onboard control system — this app never does direct motor control.
- **Drones** — aerial multispectral/RGB imagery capture and crop-health scouting (largely
  read-only), and, a materially higher-risk tier, aerial spraying/seeding or any flight beyond a
  pre-approved geofence.

### Criticality tiers

| Tier | Examples | Gated? |
|---|---|---|
| **0 — always autonomous** | Sensor reads; drone scouting imagery within an approved geofence; routine irrigation within a normal moisture/weather envelope | No — allow-listed |
| **1 — conditionally gated** | Dosing/irrigation volume outside the normal range; a robot action with a physically irreversible effect (harvesting, cutting) | Only when the specific call falls outside the safe envelope (a `classifier` evaluates the actual parameters) |
| **2 — always gated** | Any chemical spraying; any drone flight outside the approved geofence or beyond visual line of sight (real regulatory/safety exposure, not just a product risk); anything with meaningful cost or irreversibility | Always |

### The HITL gate and why it slots in almost for free

`HumanInTheLoop(allowed_tools=[...])` wraps the orchestrator's (or a new "field-ops" agent's)
`Agent(interventions=[...])`, allow-listing every Tier 0 tool and gating Tier 1/2 calls. A gated
call raises `Confirm()` with no inline response, Strands raises the interrupt, and the Lambda
invocation ends with `stop_reason="interrupt"`.

The reason this is unusually cheap to adopt *when the time comes*: Strands' interrupt state
(`_InterruptState`) is part of what a `SnapshotSessionManager` already serializes on every save.
This repo built exactly that session-resume infrastructure in Phase 7.5+
(`docs/stories/tracker-scheduler.md`, `session_id = goal_id`) for an unrelated reason (avoiding a
full-DynamoDB-reconstruction on every conversational turn) — but it turns out to be *precisely*
the missing piece that makes a multi-hour or multi-day pause-for-a-human viable across stateless
Lambda invocations, with **zero new persistence work** required.

### Resume path

A farm operator's approve or deny — critically, including a genuine **reject-with-reason** (today's
`approvePlan` has no reject path at all, only approve) — is packaged as Strands'
`interruptResponse` content shape and delivered back via a new EventBridge event. That wakes the
orchestrator, which calls `agent([{"interruptResponse": {"interruptId": ..., "response": ...}}])`,
resuming the *same paused reasoning turn* — not a fresh one — with the human's answer folded
directly back into the tool result the model sees. A denial can be reasoned about in-context (e.g.
proposing a smaller, safer alternative dose in the same turn) rather than becoming a dead end that
needs a whole new conversation.

### Why push, not poll, matters here

Today's chat UI is fine to poll — a plan revision isn't time-critical. A critical physical action
sitting in an interrupted, paused state is different: a storm rolling in, or a drone waiting on
clearance, needs the operator notified promptly. This is the first place this repo's
already-designed-but-not-yet-built WebSocket push channel (the carried-forward Notification
Lambda in `architecture.md`/`data-architecture.md`) becomes a real, load-bearing requirement
rather than a nice-to-have.

## Options Considered

### Option A: Strands-native `HumanInTheLoop` tool-call gating (recommended)

Pauses and resumes the actual reasoning turn in place; reuses the session-resume infrastructure
already built for an unrelated reason; lets the model react to a rejection *in-context*, in the
same turn, rather than starting over.

### Option B: App-level approval endpoint (mirror today's `approvePlan` pattern)

Simpler to build with what exists today — a synchronous, deterministic status flip extended to
actuator actions. But it can't pause *mid-reasoning*: a denial has to become a whole new
turn/event, losing the in-context reaction Option A gets for free, and duplicates a
pause/resume mechanism Strands already ships.

### Option C: Always require human approval for every actuator action, regardless of risk

Defeats the entire purpose of autonomous action — if every routine watering needs a human click,
there's no automation. Rejected.

### Option D: IoT stays purely advisory forever — no autonomous action at all

Rejected; contradicts the explicit premise motivating this ADR (agents should be able to act,
gated only where it's genuinely critical).

## Trade-off Analysis

Option A is the only one that gives the model itself a way to *react* to a rejection inline,
rather than treating "denied" as a dead end requiring a fresh conversation — and it costs nothing
new on the persistence side, since Phase 7.5+'s session-resume work already covers it. Its real
cost is operational, not architectural: every gated action risks an idle, paused session waiting
on a human, and that pause is only safe if a real push-notification channel exists to surface it
promptly — both flagged below as open questions this ADR deliberately does not resolve.

## Consequences

- **Easier:** autonomous routine action with a real safety valve on the risky cases, using a
  mechanism Strands ships for exactly this scenario; a genuine reject-with-reason path that
  today's plan approval still lacks; zero new session-persistence work.
- **Harder:** needs the WebSocket push channel actually built (currently carried-forward, not
  scheduled); needs a criticality-classification policy decided per tool/action before any of
  this is real; an unattended interrupt has no timeout/escalation policy yet — what happens if
  nobody responds for hours during, say, a frost event? — an open question, not answered here.
- **To revisit:** whether static per-tool criticality tiers hold up in practice, or whether the
  SDK's `classifier=True` LLM-driven risk assessment is needed sooner than assumed; regulatory
  requirements for drone operation (airspace, licensing) that exist entirely outside this
  system's control and must be satisfied regardless of what this ADR designs.

## Action Items

None scheduled — this ADR exists so the decision is recorded and ready, not to imply the work is
starting now. When real IoT/hardware integration is actually scoped, revisit:

1. [ ] Confirm which physical devices/platforms are actually in scope for the first real
   integration (a single soil-moisture sensor + one irrigation valve is a far smaller first slice
   than drones/robots — sequence accordingly, following this repo's established
   incremental-vertical-slice philosophy, `docs/roadmap.md`).
2. [ ] Design the WebSocket push channel for real (`architecture.md`'s carried-forward
   Notification Lambda) — a prerequisite, not an optional nice-to-have, for this ADR's resume path.
3. [ ] Decide the criticality-tier policy per tool/action before any actuator tool is built.
4. [ ] Decide an escalation/timeout policy for an unanswered interrupt.
5. [ ] Extend `data-architecture.md` §2 with the `Device`/`SensorReading`/`ActuatorAction`
   entities once this moves from proposed to accepted.
