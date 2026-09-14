# Tendril — Data Architecture

This document makes ADR-0002's three-tier state model concrete: the actual entity/key design,
where sessions/context/memory live and how they're keyed, the agent data-access boundary
(ADR-0013), and a full inventory of every Lambda, API, Agent, Event, and Data component in the
system as currently designed. It fulfills ADR-0002's action item 4 ("define the DynamoDB
schema... and the query patterns the tracker/scheduler need").

Where this document adds new decisions beyond restating existing ones, they're called out
explicitly with their own ADR (0013) rather than asserted here as if already accepted.

---

## 1. The three tiers, concretely

Per [ADR-0002](./ADRs/0002-context-management-and-durable-state.md):

| Tier | Mechanism | Keyed by | Lives in |
|---|---|---|---|
| Working context (in-model) | Strands `context_manager="auto"` (`SummarizingConversationManager` + `ContextOffloader`) + `ContextInjector` for pinning vision/success-criteria | the current agent invocation | in-memory during the invocation only |
| Conversational durability | Strands `SnapshotSessionManager` + `S3Storage` **(Implemented, Phase 7.5+)**; Strands `MemoryManager` + `BedrockKnowledgeBaseStore` | `session_id = goal_id`; memory `scope = "{user_id}:{garden_id}"` | **`tendril-{env}-agent-state`** S3 bucket (sessions; `ContextOffloader` context-offload still not wired, see §3.3); Bedrock Knowledge Base (memory) |
| Structured domain state | DynamoDB | `pk`/`sk` per entity (§2) | existing **`tendril-{env}-app`** table (`FoundationStack`) |

The orchestrator is the only component that touches all three tiers directly; specialists only
ever see tier 1 (their own invocation) plus whatever tier-2/3 data the orchestrator hands them as
input (§4).

---

## 2. Application entity data model (DynamoDB single-table design)

`tendril-{env}-app` (existing `FoundationStack.app_table`, `pk`/`sk` strings, on-demand billing)
holds every structured domain entity from `architecture.md` §2. Single-table, partitioned so
that **almost every read is a single-partition query scoped to one tenant** (`GARDEN#{garden_id}`
or `USER#{user_id}`) — a query can never accidentally span gardens without going through the one
deliberate GSI below.

| Entity | `pk` | `sk` | Key attributes | Notes |
|---|---|---|---|---|
| User | `USER#{user_id}` | `METADATA` | `channel`, `locale` | |
| Garden ownership (index record) | `USER#{user_id}` | `GARDEN#{garden_id}` | denormalized `name` | Lets "list my gardens" be one query with no GSI |
| Garden (canonical) | `GARDEN#{garden_id}` | `METADATA` | `name`, `vision`, `geolocation`, `climate_zone`, `owner_user_id`, `media_id?` | `media_id` added for the garden-photo hero banner (2026-09-13) — same shape as `Plant.media_id`/`Task.media_id`. Set only when a photo is picked during "Setup My Garden"; `getGarden` resolves it to a fresh presigned `photoUrl`. Also the one entity whose `garden_id` may be client-supplied (`ConditionExpression: attribute_not_exists(pk)` guards it) — needed so the frontend can request a media upload (which requires an existing `gardenId`) *before* the garden itself is created. |
| Plant | `GARDEN#{garden_id}` | `PLANT#{plant_id}` | `species`, `variety`, `stage` | |
| Goal | `GARDEN#{garden_id}` | `GOAL#{goal_id}` | `description`, `type`, `status`, `plant_id?` | `plant_id` present only if plant-scoped — **`plant_id` now actually written (images-completion pass, 2026-09-13)**; previously reserved but no code path ever set it. |
| Plan | `GARDEN#{garden_id}` | `PLAN#{goal_id}` | `plan_id`(=`goal_id`), `goal_id`, `success_criteria`, `status` | **Implemented (PA-01)**. `plan_id` reuses `goal_id` directly (always 1:1, avoids a pointless extra id) — a deviation from this table's original sketch of a separate `plan_id`. |
| Task | `GARDEN#{garden_id}` | `TASK#{goal_id}#{task_id}` | `task_id`, `plan_id`(=`goal_id`), `goal_id`, `title`, `detail`, `scope` (`plant`\|`garden`), `status`, `plant_id?`, `media_id?`, `feedback?`, `due_date?`, `gsi1pk?`, `gsi1sk?` | **Implemented (PA-01)**, keyed `TASK#{goal_id}#{task_id}` — a deviation from the flat `TASK#{task_id}` (+ `gsi1pk`/`gsi1sk`) originally sketched here for the `TasksDueIndex` GSI; that GSI is now added *alongside* this key rather than replacing it (Phase 7.5+) — this key still makes "replace this goal's task set on a plan revision" one cheap prefix `Query`, not a scan, and the GSI separately answers "which tasks are overdue" without needing to change it. `plant_id`/`media_id` added in the images-completion pass (2026-09-13): `plant_id` is inherited from the Goal's own `plant_id` when the orchestrator proposes the task (`_write_plan_and_tasks`); `media_id` is set by `postTaskCheckin`, which also flips `status` to `done`. `feedback` (PA-05) is the orchestrator's own assessment of a check-in's photo, stamped by `_update_task_feedback`. `due_date`/`gsi1pk`/`gsi1sk` (**Implemented, Phase 7.5+**) are stamped by `approvePlan` on every pending task at approval time and by the orchestrator's `handle_followup_due` on each nudge (pushed forward so the same task doesn't refire every tracker tick), and removed by `postTaskCheckin` on check-in — one check-in per task, not a repeatable progress log (a real progress *log* is still carried-forward work, see `docs/stories/tracker-scheduler.md`). **`listTasks` (Phase 6)** reuses this same key for a cross-goal list — `TASK#*` (no goal prefix) under one garden's partition is still a single cheap `Query`, no GSI needed; real due-date-based *grouping* in the Tasks screen itself is still carried-forward UI work, separate from the GSI now backing the tracker. |
| Message | `GARDEN#{garden_id}` | `GOALMSG#{goal_id}#{iso_timestamp}#{message_id}` | `message_id`, `goal_id`, `role` (`user`\|`assistant`), `content`, `created_at` | **Implemented (PA-02)** — the chat thread backing conversational plan approval; not in the original design, added when PA-02 was scoped. `sk` is time-sortable, same trick as `Event` below. |
| Tracking | `GARDEN#{garden_id}` | `TRACKING#{tracking_id}` | `goal_id`, `plan_id?`, `task_id?`, `timestamp`, `observation`, `decision` | Not yet implemented — Phase 6/7+. |
| Event (capture-first log) | `GARDEN#{garden_id}` | `EVENT#{iso_timestamp}#{event_id}` | `type`, `payload`, `created_at` | **Implemented (Phase 6)**. `sk` is time-sortable — `listActivity` queries it with `ScanIndexForward=False` for a newest-first feed, directly backing the frontend's Activity screen. Written best-effort (never fails the primary request) at 4 call sites: `createGoal` (`goal.submitted`), `approvePlan` (`plan.approved`), `postTaskCheckin` (`task.checkin`), and the orchestrator's `_write_plan_and_tasks` (`plan.updated`, covering both first proposal and every revision). Deliberately UI-agnostic — no icon/tone/title baked in server-side; the frontend maps `type` to presentation (`activity-presentation.util.ts`). |
| Media | `GARDEN#{garden_id}` | `MEDIA#{media_id}` | `s3_key`, `content_type`, `plant_id?`, `goal_id?`, `task_id?`, `uploaded_at` | `s3_key` points into `MediaBucket`. `getGoalDetail` (PA-03) generates a fresh presigned GET url per read — never stored. `goal_id?` and `task_id?` added in the images-completion pass (2026-09-13) — `goal_id` is set transactionally by `createGoal` for every attached media (alongside `plant_id`, if the goal has one); `task_id` is set by `postTaskCheckin`. A Media record can now be traced to whichever of Plant/Goal/Task it's actually evidence for, completing the traceability chain `data-architecture.md` had reserved fields for but no code had ever populated. |
| Notification | `USER#{user_id}` | `NOTIFICATION#{sent_at}#{notification_id}` | `channel`, `status`, `related_goal_id?`, `related_task_id?` | scoped by user, not garden — a user may have several gardens. Not yet implemented. |

### GSI: `TasksDueIndex` (on `AppTable`) — **Implemented (Phase 7.5+)**

The tracker/scheduler's defining query — "which tasks have a follow-up due right now?" — can't be
answered by the table's own `pk`/`sk` (that would mean scanning every garden's partition). A
**sparse GSI**: `gsi1pk = "TASK_STATUS#{status}"` (only ever `TASK_STATUS#pending` in practice),
`gsi1sk = due_date` (ISO 8601). Only tasks actually awaiting a follow-up carry these two
attributes — completed/abandoned tasks omit them, so the index stays small. `app/tracker/`'s
scheduled Lambda (`EventBridge Scheduler`, 15-minute rate) queries
`gsi1pk = "TASK_STATUS#pending" AND gsi1sk <= now` and emits one `followup.due` event per hit
(§6.4), paginating across `LastEvaluatedKey` so a truncated page never silently drops an overdue
task. `approvePlan` stamps these attributes onto every pending task at approval time; the
orchestrator's `handle_followup_due` bumps them forward on each nudge (so the same task doesn't
refire every tick); `postTaskCheckin` removes them on check-in.

### `ConnectionsTable` (existing, `FoundationStack.connections_table`)

`pk = connectionId`, TTL on `ttl`. Add a **GSI `UserConnectionsIndex`** on `userId` (an attribute
already needed on connect) so the notifier can answer "which open sockets belong to this user?"
when pushing a WebSocket update — the reverse lookup ADR-0004's design implies but didn't spell out.

---

## 3. Sessions, context, and memory — where they actually live

### 3.1 Orchestrator session (cross-Lambda-invocation resume) — **Implemented (Phase 7.5+)**

The orchestrator is stateless Lambda, woken repeatedly by EventBridge for the *same* goal (a
submission, a chat message, a follow-up nudge, days apart). Session identity is the mechanism
that stitches these invocations back into one continuous agent:

```python
session_manager = SnapshotSessionManager(
    session_id=goal_id,
    storage=S3Storage(bucket=AGENT_STATE_BUCKET_NAME, prefix="orchestrator-sessions/"),
)
agent = Agent(session_manager=session_manager, ...)
```

**Deviation from the original sketch above the code block:** no `{env_name}` segment in the S3
key prefix. The bucket *name* (`tendril-{env}-agent-state`) already physically separates
environments — repeating `{env_name}` again inside the object-key prefix would be redundant
nesting with zero isolation benefit, since dev and prod are never the same bucket.

Every EventBridge wake for this `goal_id` reconstitutes the same messages, agent state, and
conversation-manager state via `_build_orchestrator_agent` (`app/orchestrator/orchestrator.py`).
Each handler's prompt is now just the genuinely new information for that turn — not a full
transcript restatement (`docs/stories/tracker-scheduler.md`'s SR-02/SR-03; `_format_transcript`
was deleted). A hand-rolled `_strip_trailing_unresolved_tool_use` runs immediately after `Agent`
construction — no `strip_trailing_tool_use()` function exists in the installed `strands-agents`
SDK; this implements the documented mitigation pattern directly, removing a trailing unresolved
`toolUse` block from restored history (defense-in-depth, even though only this orchestrator ever
writes to its own session today).

Uses the dedicated `tendril-{env}-agent-state` S3 bucket, separate from `MediaBucket` (different
IAM consumers per ADR-0013: only the orchestrator touches this one; the frontend's
presigned-upload flow touches `MediaBucket`).

### 3.2 Memory — two knowledge bases, not one

Strands `MemoryManager` (ADR-0001 Appendix A / AF-05) needs a `MemoryStore`. Tendril needs two,
serving different purposes, both accessed via direct `bedrock:Retrieve`/
`bedrock:IngestKnowledgeBaseDocuments` calls (the narrow exception in ADR-0013 — a Knowledge Base
is already an API-fronted, per-resource-scoped managed service):

| Store | Purpose | Scoping | Writable |
|---|---|---|---|
| **Garden Memory** | "Remember everything about *this* garden" — this user's past goals, what worked, seasonal patterns | `scope = "{user_id}:{garden_id}"` (Strands `scope` param, stamped on every write, applied as a retrieval filter) — this **is** the multi-tenant isolation mechanism, not a separate scheme | Yes (`data_source_type: CUSTOM`) |
| **Horticultural Reference** | Curated, expert-authored horticultural knowledge — the "Knowledge search" tool every specialist can use (architecture.md §4.2) | None — shared, read-only across all tenants | No (read-only; content is authored/updated out of band by domain experts, per ADR-0006's externalized-expertise principle) |

Both agents and the orchestrator may query **Garden Memory** (scoped to the current
`user_id`/`garden_id` from the orchestrator's own invocation context). Only the orchestrator (and
any specialist needing it) queries **Horticultural Reference** — read-only, no scoping needed.

### 3.3 Context offloading — **not in scope for Phase 7.5+**

`ContextOffloader` (part of `context_manager="auto"`) still needs to be pointed at the **same**
`tendril-{env}-agent-state` bucket, under a distinct prefix (`context-offload/`) — left at its
default in-memory backend, offloaded content (large vision/weather tool results) would vanish
between Lambda invocations. Phase 7.5+ only wired session-resume (§3.1); `ContextOffloader`/
`ContextInjector` pinning remain open (ADR-0002 action item 2), tracked in
`docs/stories/tracker-scheduler.md`'s "Carried forward" section.

---

## 4. Media handling — how a specialist "sees" a photo without S3 IAM

Per [ADR-0013](./ADRs/0013-agent-data-access-boundary.md), specialists get no direct S3 access.
The flow:

1. Frontend requests a presigned **PUT** URL from the Client API (`POST /gardens/{id}/media`,
   ADR-0004/WS-01/WS-03) and uploads directly to `MediaBucket`. A `Media` record is written to
   `AppTable` (§2).
2. When the orchestrator needs a specialist to analyze a photo, **the orchestrator** (which does
   hold `MediaBucket` IAM) either (a) fetches the object and passes the bytes/base64 as part of
   the `InvokeAgentRuntime` tool-call payload, or (b) generates a short-lived presigned **GET**
   URL and passes that URL — in which case the specialist fetches it over plain HTTPS, exactly
   like calling any other Tool API, **not** via an S3 IAM grant.
3. Which of (a)/(b) is used is a size/latency trade-off (AgentCore invocation payload limits vs.
   an extra HTTP round-trip) to decide when the Vision/Diagnosis specialist is actually built —
   flagged as an open item (§10), not decided here.

---

## 5. Agent data-access boundary (summary of ADR-0013)

| Component | `AppTable` / `ConnectionsTable` | `MediaBucket` | `AgentStateBucket` | Bedrock Knowledge Bases | Tool APIs |
|---|---|---|---|---|---|
| **Orchestrator** (Lambda) | Direct IAM, read/write | Direct IAM, read (+ presigned GET generation) | Direct IAM (its own session state) | Direct IAM (both KBs) | Calls them like any other agent-side capability when needed (e.g., its own HITL `ask` uses the Notification tool) |
| **Specialist agents** (AgentCore) | **None.** Data arrives as tool-call input from the orchestrator | **None.** Photo content arrives as input or via a passed presigned URL fetched over HTTPS | **None** — specialists don't have their own cross-invocation session | Direct IAM, scoped to the specific KB(s) its registry entry needs | Only the tools its own `agents/registry/*.json` entry declares (AF-01) |
| **Client API / Ingestion Lambdas** | Direct IAM, read/write | Direct IAM (presigned URL generation, S3 event handling) | — | — | — |
| **Tracker/Scheduler Lambda** | Direct IAM, read (queries `TasksDueIndex`) | — | — | — | — |
| **Notification Lambda** | Direct IAM, read/write (Notification entity) | — | — | — | — |
| **Tool API Lambdas** | **None** (they're capabilities, not data owners — a tool that needs its own state gets its own narrowly-scoped resource, decided per tool, not a blanket grant) | — | — | — | — |

---

## 6. Full component inventory

### 6.1 Lambda functions

| Lambda | Home | Triggered by | Reads/writes | Status |
|---|---|---|---|---|
| Client API handlers (media-upload, goal-intake, garden/plant/task CRUD, plan-approve, status) | `app/api/` | API Gateway (`SpecRestApi`, ADR-0011) | `AppTable`, `MediaBucket` (presigned URLs) | WS-01/WS-03: media-upload + goal-intake in scope; the rest of ADR-0004's endpoint table is future work |
| Ingestion | `app/ingestion/` | Client API calls; `media.uploaded` S3 event | `AppTable`, `MediaBucket` | Named in architecture.md §3; not yet scaffolded |
| **Orchestrator** | `app/orchestrator/` | EventBridge (`goal.submitted`, `plan.approval.responded`, `followup.due`, `followup.reply.received`) | `AppTable`, `ConnectionsTable`, `MediaBucket`, `AgentStateBucket`, both Knowledge Bases; invokes specialists via `InvokeAgentRuntime` and tools via HTTP | WS-04 (walking-skeleton proof), ADR-0012 |
| Tracker / Scheduler | `app/tracker/` | EventBridge Scheduler (15-minute rate) | `AppTable` (`TasksDueIndex` GSI, read); EventBridge (`followup.due`, write) | **Implemented (Phase 7.5+)** — a plain boto3 Lambda, no `strands-agents`; publishes one `followup.due` event per overdue pending task for the orchestrator's `handle_followup_due` to act on. |
| Notification | `app/notifier/` | Tracker (direct); Orchestrator (as a Tool API / its HITL `ask` channel); inbound replies | `AppTable` (Notification entity), `ConnectionsTable` (WebSocket push) | In-app/WebSocket + web-push near-term; WhatsApp/email **deprioritized** (ADR-0001 refinement, 2026-09-12) |
| WebSocket connect/disconnect/route handlers | `app/api/ws/` | API Gateway WebSocket lifecycle | `ConnectionsTable` | ADR-0004; not yet scaffolded |
| Tool API: **Weather** | `tools/weather/` | API Gateway / Function URL | none of its own (stateless call-through to a weather provider) | AF-03 reference implementation |
| Tool API: Plant-ID / Vision | `tools/plant-vision/` | API Gateway / Function URL | none | Future (AF follow-up) |
| Tool API: Nursery / Market | `tools/nursery-market/` | API Gateway / Function URL | none | Future |

`tools/` is a **new top-level monorepo folder** (sibling to `agents/`, `app/`, `infra/`), since
each Tool API is its own self-contained deployable unit (CLAUDE.md's monorepo convention) — not
previously named explicitly in AF-03.

### 6.2 APIs

| API | Type | Purpose |
|---|---|---|
| Client API | REST, API Gateway `SpecRestApi` from `app/api/openapi.yaml` | User-facing entry (ADR-0011) |
| WebSocket API | API Gateway WebSocket | Real-time push to the frontend (ADR-0004); primary HITL delivery channel (2026-09-12 reprioritization) |
| Tool APIs | One shared "Tools" API Gateway with one route per tool (`/tools/weather`, `/tools/plant-vision`, ...), or Lambda Function URLs per tool if a given tool's auth/scaling needs diverge | Agent-facing capabilities (architecture.md §4.2/§4.3) |

### 6.3 Agents

| Agent | Runtime | Registry entry | Status |
|---|---|---|---|
| Orchestrator | Lambda (Strands) | N/A — not a registered specialist | WS-04 |
| `hello` | AgentCore (shared template) | `agents/registry/hello.json` | Walking-skeleton stand-in (WS-04) |
| `agronomy` | AgentCore (shared template) | `agents/registry/agronomy.json` | Agent Factory proof specialist (AF-01/02/04) |
| pest, disease, irrigation, fertilizer, pruning, weather-impact, beautification/landscaping | AgentCore (shared template) | not yet authored | Carried forward — each is a near-copy of `agronomy.json` + its own prompt + guardrail (`docs/stories/agent-factory.md`) |

### 6.4 Events (EventBridge)

| Event (`detail-type`) | Source | Target | Carries |
|---|---|---|---|
| `goal.submitted` | Client API / Ingestion | Orchestrator | `garden_id`, `goal_id` |
| `goal.message.received` | Client API (`/goals/{id}/messages`) | Orchestrator | `garden_id`, `goal_id` — **Implemented (PA-02)**. The orchestrator reloads the goal/plan/message history from `AppTable` rather than the event carrying it, same posture as `goal.submitted`. |
| `followup.due` | Tracker/Scheduler | Orchestrator | `garden_id`, `goal_id`, `task_id` |
| `followup.reply.received` | Notification | Orchestrator | `garden_id`, `goal_id`, reply content/media reference |
| `media.uploaded` | S3 event notification on `MediaBucket` | Ingestion | `garden_id`, `media_id`, `s3_key` |

**`plan.approval.responded` — not implemented as an event (PA-02 deviation):** approving a plan
needs no model reasoning, only a deterministic status flip, so `POST /plans/{id}/approve` is
handled synchronously in the Client API Lambda itself — it never publishes an event or reaches
the orchestrator at all. This row is deliberately removed from the table above; §7 below reflects
the real flow.

Every orchestrator-bound event carries both `garden_id` and `goal_id` so the orchestrator's
`AppTable` reads stay single-partition queries (§2) — never a lookup by `goal_id` alone.

### 6.5 Data stores

| Store | Kind | Owner(s) | Notes |
|---|---|---|---|
| `tendril-{env}-app` | DynamoDB (single-table + `TasksDueIndex` GSI) | Client API, Ingestion, Orchestrator, Tracker, Notification | Existing (`FoundationStack`); GSI (`TasksDueIndex`) implemented (§2) |
| `tendril-{env}-ws-connections` | DynamoDB (+ `UserConnectionsIndex` GSI) | WebSocket handlers, Notification | Existing (`FoundationStack`); GSI still open (§2) |
| `tendril-{env}-media` | S3 | Client API (presigned URLs), Ingestion, Orchestrator (read) | Existing (`FoundationStack`) |
| `tendril-{env}-agent-state` | S3 | Orchestrator only | **Implemented (Phase 7.5+)** — `SnapshotSessionManager` session snapshots wired in (§3.1); context offload (§3.3) still not wired |
| Garden Memory (Bedrock KB) | Managed | Orchestrator, specialists (scoped) | **New** — per-tenant `scope` (§3.2) |
| Horticultural Reference (Bedrock KB) | Managed | Orchestrator, specialists (read-only) | **New** — shared, curated (§3.2) |
| Secrets Manager / SSM | Managed | Any Lambda needing a secret | Existing pattern (`app/common/config.py`, ADR-0008) |

---

## 7. Data flow: goal submission → orchestrator → specialist → conversation → approval

**As actually implemented (PA-01/PA-02/Phase 7.5+) — see those stories' Context notes for why
this departs from the original sketch below it:**

```
User (UI) --POST /gardens/{id}/media--> Client API --presigned PUT--> MediaBucket
User (UI) --POST /gardens/{id}/goals--> Client API
    Client API: validate, write Goal (status=Intake) to AppTable, write Media record
    Client API --EventBridge: goal.submitted (garden_id, goal_id)--> Orchestrator
Orchestrator (Agent with a SnapshotSessionManager attached, session_id=goal_id — Phase 7.5+):
    read Goal + Media from AppTable (direct IAM)
    InvokeAgentRuntime -> whichever specialist(s) are relevant, chaining vision's identification
        into the others when a photo is attached
    Orchestrator's own agent: one normal turn (tool-calling), then one prompt-less
        structured_output_model call on the same agent instance to extract {reply, updated_plan?}
    If updated_plan: writes Plan + Tasks (status=PlanProposed) to AppTable; Goal.status=PlanProposed
    Writes an assistant Message either way
User (UI) --POST /gardens/{id}/goals/{id}/messages--> Client API
    Client API: write a user Message, --EventBridge: goal.message.received (garden_id, goal_id)--> Orchestrator
Orchestrator: resumes the SAME session (session_id=goal_id) — the prior conversation is already
    in the restored agent's history, so the prompt for this turn is just the newest Message's
    text, not a full transcript restatement. Runs the same turn shape as above — may revise the
    Plan, or just ask a clarifying question (updated_plan absent)
User (UI) --POST /gardens/{id}/plans/{id}/approve--> Client API
    Client API: synchronous update_item, Plan.status=Approved, Goal.status=Approved — no event,
    no model call (approval needs no reasoning, only a deterministic status flip); also stamps
    due_date/gsi1pk/gsi1sk (Phase 7.5+) on every pending Task
Tracker (EventBridge Scheduler, 15-min rate) --queries TasksDueIndex--> emits followup.due for
    each overdue task --EventBridge--> Orchestrator
Orchestrator's handle_followup_due (Phase 7.5+, no model call): writes a deterministic nudge
    Message + Activity Event, bumps the task's due-date forward
```

**As originally sketched (not built — kept for context on what's still open):** a WebSocket push
of the proposed plan — still carried-forward, future work. (Session-resume and the tracker/
scheduler, also originally sketched here, are now implemented — Phase 7.5+, `docs/backlog.md`.)

---

## 8. Multi-tenancy & privacy

Every data-store access pattern in this document is scoped to one tenant by construction, not by
convention alone:

- DynamoDB: partition key is always `GARDEN#{garden_id}` or `USER#{user_id}` — a query can only
  ever see one tenant's items (the one GSI, `TasksDueIndex`, is scoped by `status`+`due_date`,
  never returns cross-tenant *content*, only which garden/goal/task to look up next — the
  orchestrator's follow-up `AppTable` read is still single-partition).
- Memory: the `scope` parameter *is* the isolation boundary (§3.2) — not a separate access-control
  layer bolted on afterward.
- Media: presigned URLs are per-object and short-lived; specialists never hold standing S3 access
  (§4/§5).
- This satisfies architecture.md §6.3's "memory/storage stores and DynamoDB keys scoped per
  user/garden" and ADR-0002's privacy-by-design requirement without new mechanism — the natural
  key design already enforces it.

---

## 9. Open items / follow-ups

1. ~~Provision `tendril-{env}-agent-state` (S3)~~ — **done, Phase 7.5+** (`FoundationStack`).
2. Provision the two Bedrock Knowledge Bases (Garden Memory, Horticultural Reference) and their
   IAM — Garden Memory done (AF-05, `MemoryStack`); Horticultural Reference still open.
3. ~~Add `TasksDueIndex` (on `AppTable`)~~ — **done, Phase 7.5+**; `UserConnectionsIndex` (on
   `ConnectionsTable`) remains open, unrelated to this epic.
4. Decide the media-to-specialist strategy (bytes-in-payload vs. presigned-URL-fetch, §4) when
   the Vision/Diagnosis specialist is actually built. *(Resolved in practice: presigned-URL-fetch
   — `orchestrator.py`'s `_resolve_image` generates a short-lived GET url and passes it to
   specialists as `imageUrl`.)*
5. Decide `tools/` API Gateway topology (one shared Gateway vs. per-tool Function URLs) when the
   second tool (beyond Weather) is built.
6. ~~CDK assertion tests enforcing ADR-0013~~ — **done, Phase 7.5+**
   (`test_no_specialist_role_has_dynamodb_or_s3_iam_actions`, `infra/tests/test_stacks.py`).

## 10. Related documents

- [ADR-0002 (context management & durable state)](./ADRs/0002-context-management-and-durable-state.md)
- [ADR-0013 (agent data-access boundary)](./ADRs/0013-agent-data-access-boundary.md)
- [ADR-0004 (backend API, serverless & storage)](./ADRs/0004-backend-api-serverless-storage.md)
- [ADR-0012 (orchestrator + declarative agent registry)](./ADRs/0012-orchestrator-lambda-declarative-agent-registry.md)
- [Strands capability mapping](./strands-capability-mapping.md) (session/memory/context mechanics)
- [ADR-0015 (IoT-driven autonomous actuation & Strands-native HITL, future state — Proposed)](./ADRs/0015-iot-actuation-and-human-in-the-loop.md)
- [`architecture.md`](./architecture.md) §2 (domain model), §4 (APIs)
