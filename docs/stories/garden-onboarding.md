# Stories — Epic: Garden Onboarding

The two features every other story implicitly assumes already happened: a garden exists, and it
has plants. Nothing here touches the orchestrator or any agent — this is plain CRUD, deliberately
the **first** thing built (per the 2026-09-12 UI-first incremental roadmap,
[`docs/roadmap.md`](../roadmap.md)) because it's the simplest possible full vertical slice (UI →
API → Lambda → data) to prove the whole pipe end-to-end before any AI complexity enters.

**Story format:** `As a <role>, I want <capability>, so that <benefit>` + Acceptance Criteria +
Tasks + Dependencies + Status.

**No auth yet** (ADR-0004's seam is still open): both stories use a per-browser anonymous
`user_id` (`crypto.randomUUID()`, persisted in `localStorage`, sent as an `X-User-Id` header)
until Cognito lands. This is the "lightweight/anonymous identifier in dev" ADR-0004 already
called for — not a new decision.

**Suggested order:** OB-01 → OB-02.

**Status legend:** ✅ done · ◐ partially done · ☐ to do.

---

## OB-01 — Setup My Garden

**As a** new gardener, **I want** to create a named garden with a location and short
description, **so that** everything else (plants, goals, plans) has somewhere to attach to.

**Acceptance Criteria**
- [x] A new **"Create Garden"** screen/form (`frontend/src/app/features/garden-setup/`, route
  `/garden-setup`) collects: `name` (required), `geolocation` (required — free text for now, e.g.
  "Pune"; structured lat/lon is a later refinement, not blocking this story), `vision` (optional
  short description, e.g. "fresh organic veggies from my balcony").
- [x] `app/api/openapi.yaml` gains `POST /gardens` (request: `{name, geolocation, vision?}` →
  response: `{gardenId}`, 201) and `GET /gardens/{gardenId}` (returns the full `Garden` schema) —
  the first two real operations added to the contract (ADR-0011).
- [x] The **first** `ClientApiStack` is provisioned (`SpecRestApi` importing the patched
  `openapi.yaml`, per ADR-0011's synth-time ARN-injection pattern) — this is the first time any
  Client API infra actually exists; later stories (OB-02, WS-*) add operations to this same
  stack, not new ones.
- [x] The garden-create Lambda handler writes **both** DynamoDB records in one
  `TransactWriteItems` call (data-architecture.md §2): the canonical
  `pk=GARDEN#{garden_id}, sk=METADATA` record, and the ownership index
  `pk=USER#{user_id}, sk=GARDEN#{garden_id}` record — never one without the other.
- [x] Malformed input (missing `name`/`geolocation`) is rejected with a typed 4xx — never an
  unhandled exception.
- [x] After creation, the frontend navigates to `/home` (or `/garden`) with the new garden as
  the active context (a simple app-level "current garden" signal/service — no multi-garden
  switcher UI yet; that's carried forward).
- [x] Unit tests: valid create, missing-field validation, the transactional double-write.
- [x] Component tests: form validation, successful submit navigates away, a failed submit shows
  a recoverable error.

**Tasks**
- [x] Add the two operations to `openapi.yaml` + `components.schemas.Garden`.
- [x] Stand up `ClientApiStack` (new `infra/stacks/client_api_stack.py`) importing the spec.
- [x] Implement the garden-create/read Lambda handler(s) in `app/api/`.
- [x] Build the `garden-setup` frontend feature (form + a `GardenSetupApi`/extension of the
  existing `GardenApi` interface, replacing the relevant slice of `MockGardenApi`).
- [x] Wire the `X-User-Id` header convention client-side (generate once, persist, attach to
  every Client API call — a small shared interceptor, not per-request boilerplate).

**Dependencies:** none (first feature). **Status:** ✅ done (implemented; not yet deployed to
dev — deploy is a separate, explicit step per this repo's practice of asking before any deploy).

---

## OB-02 — Add a Plant (with a photo)

**As a** gardener, **I want** to add a plant to my garden with a photo, species, and variety,
**so that** I have something concrete for later goals/diagnoses to reference.

**Acceptance Criteria**
- [x] The Garden screen (`frontend/src/app/features/garden/`) gains an "Add plant" action opening
  a form: `species` (required), `variety` (optional), a **photo** (camera capture or local file
  picker — same capture UI pattern originally scoped for WS-05, built here first since it's
  needed now).
- [x] `openapi.yaml` gains **`POST /gardens/{gardenId}/media`** (request `{contentType,
  fileName}` → response `{uploadUrl, mediaId}`, a presigned S3 **PUT** URL against
  `FoundationStack`'s existing media bucket) and **`POST /gardens/{gardenId}/plants`** (request
  `{species, variety?, mediaId?}` → response `{plantId}`). **This is the shared media-upload
  primitive every later photo-involving story reuses** (WS-01's capture flow no longer needs to
  define it — see `docs/roadmap.md`'s resequencing note).
- [x] The plant-create Lambda writes the `Plant` record (`pk=GARDEN#{garden_id},
  sk=PLANT#{plant_id}`) and, if a photo was attached, the `Media` record
  (`sk=MEDIA#{media_id}`) — per data-architecture.md §2. **Implementation note:** the `Media`
  record is actually written by `createMediaUpload` (the only step that has `s3_key`/
  `content_type`); `createPlant` atomically links `plant_id` onto that same record via
  `TransactWriteItems` when a `mediaId` is given, rather than creating a second Media record —
  satisfies the same requirement (a Plant is never linked to a Media record that doesn't exist)
  given what's actually available at each step.
- [x] The frontend uploads the photo **directly to S3** using the presigned URL — never through
  the Lambda.
- [x] The Garden screen's plant list reflects real data immediately after adding one. **Scope
  note:** there's no `GET`-list-plants operation in this story (only create), so this is done by
  prepending each created plant onto the still-mocked base list client-side
  (`HttpGardenApi.createPlant`), not by re-fetching from a real list endpoint — a real list read
  is separate, later work once something actually needs it.
- [x] Camera-permission-denied and S3-upload-failure both show a recoverable error, not a silent
  failure (same bar as originally set for WS-05). **Implementation note:** the photo picker is a
  plain `<input type="file" capture>`, not `getUserMedia` — this app is deployed over plain HTTP
  (ADR-0010) where `getUserMedia` doesn't exist at all (same class of bug already found and
  fixed for `crypto.randomUUID()`), so there's no separate JS-catchable "permission denied" path
  to handle; the OS/native camera chooser handles that itself. S3-upload-failure is handled.
- [x] Unit tests: presigned-URL handler, plant-create handler (with and without `mediaId`).
- [x] Component tests: file-picker fallback, successful add-plant happy path (mocked
  `HttpClient`), one failure path.

**Tasks**
- [x] Add `Media`/`Plant` schemas and the two operations to `openapi.yaml`; extend
  `ClientApiStack` (no new stack — OB-01 already stood it up).
- [x] Implement the presigned-upload and plant-create Lambda handlers.
- [x] Build the camera-capture-or-file-picker UI component (shared — WS-05's Capture screen will
  reuse this exact component, not reimplement it).
- [x] Wire the Garden screen's "Add plant" flow end-to-end.

**Dependencies:** OB-01 (needs a garden to add a plant to; reuses its `ClientApiStack`).
**Status:** ✅ done (implemented; not yet deployed to dev).

---

## OB-03 — Show the real plant photo (not a generic icon)

**As a** gardener, **I want** to actually see the photo I uploaded for a plant, **so that** I
can visually recognize it instead of a generic icon standing in for every plant.

**Context:** OB-02 uploads the photo and links it to the plant (`Media.plant_id`), but nothing
ever reads it back — `MediaBucket` is private (`BLOCK_ALL`, ADR-0004/ADR-0013) and only a
presigned **upload** (PUT) URL exists; there's no presigned **download** (GET) URL and no
list-plants read endpoint at all yet, so today's plant rows always show `Plant.icon` (a generic
Material icon), never the real photo. **Explicitly rejected during design:** copying photos into
the public frontend/website bucket to sidestep this — that would make every uploaded photo
permanently, unauthenticated-ly public with no way to un-share it, reversing the private-media
posture ADR-0004/ADR-0013 already established on purpose. Photos stay in the private bucket;
only short-lived presigned GET URLs are handed out, same pattern as the existing upload flow.

**Acceptance Criteria**
- [ ] `createPlant`'s response includes a `photoUrl` (short-lived presigned GET URL) when the
  plant has a linked Media record — enough for the plant just added in the current session to
  show its real photo immediately (no new read endpoint needed for this part).
- [ ] A real `GET /gardens/{gardenId}/plants` list operation exists, returning each plant with a
  freshly-generated `photoUrl` per read (presigned URLs expire, so a stored URL is never reused
  across requests) — this is what makes photos still visible after a page reload, not just
  right after upload. Replaces `HttpGardenApi`'s current client-side "prepend the newly created
  plant onto a mocked list" workaround (OB-02's scope note) with a real read.
- [ ] Plants with no attached photo keep showing the existing generic icon — `photoUrl` is
  always optional, never a hard requirement.
- [ ] An expired/broken `photoUrl` (e.g. the frontend held onto it too long) falls back to the
  generic icon rather than a broken-image glyph.
- [ ] Unit tests: presigned GET URL generation, the list-plants handler (with and without a
  linked photo).
- [ ] Component tests: a plant with `photoUrl` renders the image instead of the icon; an image
  load error (`(error)` on `<img>`) falls back to the icon.

**Tasks**
- [ ] Add `photoUrl` (optional) to `PlantCreateResponse` and a new `Plant`-list schema in
  `openapi.yaml`; add the `GET /gardens/{gardenId}/plants` operation + CORS preflight.
- [ ] `garden_handler.py`: generate the presigned GET URL (reuse `_get_s3()`) wherever a plant's
  linked Media record is known; implement the list-plants handler.
- [ ] Frontend: add `photoUrl?: string` to the `Plant` model; `PlantRowComponent`/
  `PlantCardComponent` render an `<img [src]>` when present (with an `(error)` handler falling
  back to the icon), otherwise the existing icon; `HttpGardenApi.getPlants()`/
  `getPlantsSummary()` call the real list endpoint instead of merging a client-side-only array
  onto the mock.

**Dependencies:** OB-02 (needs the upload + Media↔Plant link it already built).
**Status:** ☐ to do.

---

### Definition of Done (applies to every story)

Per [`../engineering-best-practices.md`](../engineering-best-practices.md): CI green (lint +
tests + secret scan), **no hardcoded secrets/account identifiers**, deploys cleanly to **dev**
via the pipeline, touches **only the folders it needs**, updates relevant **docs/ADRs**, and is
**human-reviewed**.

### Carried forward (not in this epic)

Structured (lat/lon) geolocation input; a multi-garden switcher in the UI (the top bar's "Balcony
Kitchen Garden ▾" button is currently inert — wiring it to a real garden list is future work);
editing/deleting a garden or plant; garden/plant photo galleries beyond the single onboarding
photo.
