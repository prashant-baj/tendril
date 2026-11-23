# ADR-0033: Photo ingestion and a reusable image-processing tool

**Status:** Accepted (2026-11-23, project owner). Owner decisions: uploads up to 10 MB; analysis
copy 2048 px / 1.5 MB; image processing as a reusable tool with a resize function.
**Date:** 2026-11-23
**Deciders:** Project owner / lead engineer
**Stories:** prerequisite for CP-04 (migration), CP-05 (plant timeline), CP-08/ADR-0034 (identify the
plant, conversational check-in); later WhatsApp intake (CP-09)

## Context

Photos are the system's main input, and today they reach the models exactly as the browser
uploaded them:

1. `createMediaUpload` signs a presigned PUT for `{garden_id}/{media_id}/{file_name}` and records
   `content_type` **as declared by the client** (`file.type`, which browsers derive from the file
   extension). The orchestrator's `_resolve_image` maps that declared type to an image format and
   passes a presigned GET of the **original** to every specialist; the shared agent template fetches
   up to 5 MB and sends the bytes to the model with that format.
2. Nothing checks the real format or size, and nothing limits upload size. Bedrock's Converse API
   caps each image (3.75 MB and 8,000 px per side for most models, per the Converse `ImageBlock`
   docs); the vision model (Qwen3-VL) rejected a 4.6 MB image in practice with "Failed to buffer the
   request body: length limit exceeded".

**Observed in `dev` on 2026-11-23 (garden "Coconut Baug"):** `2026-11-10_plant10.jpg` is a PNG with a
`.jpg` name, 4.6 MB. It was recorded as `image/jpeg`; agronomy failed with "detected file MIME type
image/png does not match the expected type image/jpeg" and vision with the length limit. Because
the orchestrator gives the goal's photo to **every** specialist it consults, one bad photo made 11 of
15 specialist calls fail, and the reply fell back to "all specialist tools are unavailable". A real
0.67 MB JPEG in the same garden worked.

**The owner's archive (the CP-04 migration source) makes this the normal case:** of 137 `.jpg`
files, **86 are PNG** underneath; the median size is **3.97 MB** and **78 exceed 3.75 MB** (max 4.85
MB). Future sources (WhatsApp, iPhone HEIC) add more formats.

**Owner decisions (2026-11-23):** uploads may be **up to 10 MB**; the copy used for analysis is
**2048 px long edge, at most 1.5 MB**; image processing is a **reusable tool with a resize function**,
not code buried in one Lambda. Resizing to 2048 px keeps leaf detail and a stake number readable,
and anything above the model limit cannot be analyzed at all, so the full-resolution original is
kept as the record and the analysis runs on the processed copy.

This adds new components (a tool and an S3-triggered Lambda) and changes the media data shape and
upload contract, so it is architecturally significant. `data-architecture.md` already plans an
**Ingestion** Lambda for the `media.uploaded` S3 event (§6.1, §6.4) and grants it `AppTable` +
`MediaBucket` access in the ADR-0013 boundary table (§5); it was never built.

## Decision

### 1. An Image Tool API: stateless, reusable image processing

A new Tool API, **`app/tools/image/`**, following the existing tool pattern ("tools are APIs",
architecture §4.2; same shape as `app/tools/weather/`): a container-image Lambda (ADR-0014) with an
IAM-authenticated Function URL, discovered and deployed by `AgentCoreStack`'s `app/tools/*` loop,
its own `openapi.yaml` contract.

- **`inspect`**: detect the **real** format from magic bytes (never the declared type or extension),
  plus width, height and size.
- **`resize`**: decode (Pillow, plus `pillow-heif` for HEIC), apply EXIF orientation, convert to RGB,
  scale so the long edge is at most `maxLongEdge`, encode as JPEG stepping quality down (85 → 70)
  until at most `maxBytes`, and strip all metadata (EXIF, including GPS) unless told to keep it.
  Several outputs can be produced from one decode (for example the analysis copy and a thumbnail).
- **Input:** the source image by **presigned GET URL** (at most 10 MB; larger is rejected), or inline
  base64 for small images.
- **Output:** each result is written to a caller-supplied **presigned PUT URL**, or returned as
  base64 when no destination is given (bounded by the 1.5 MB target, well within the Function URL
  response limit). The response always carries metadata: detected format and, per output, width,
  height and bytes.
- **The tool owns no data and holds no S3 or DynamoDB permission.** Callers grant access through
  presigned URLs, exactly like specialists receive photos today (ADR-0013: "tools are
  capabilities, not data owners").
- **Reusable by any component:** the Ingestion Lambda (below), the CP-04 migration script, and later
  specialists (a registry entry can declare `tools: ["image"]`, for example so `plant_identifier`
  could ask for a zoomed crop around a stake in a later change). Backend callers invoke it with
  `lambda:InvokeFunction`; agents use the existing SigV4 Function URL binding.
- **Limits:** 10 MB source, decompression-bomb guard (more than 60 MP is rejected), 1024 MB memory,
  30 s timeout.

### 2. An Ingestion Lambda processes every upload once, using the tool

- **Trigger:** S3 `ObjectCreated` on `MediaBucket`, **prefix `uploads/`** only. New uploads move under
  that prefix: `createMediaUpload` signs `uploads/{garden_id}/{media_id}/{file_name}`. Processed files
  go under `derived/`, which the trigger never matches, so the function cannot trigger itself.
- **Work:** for each upload it creates a presigned GET for the original and presigned PUTs for
  `derived/{garden_id}/{media_id}/analysis.jpg` and `…/thumb.jpg`, then calls the Image Tool's
  `resize` once with two outputs:
  - **analysis copy:** `maxLongEdge 2048`, `maxBytes 1.5 MB`;
  - **thumbnail:** `maxLongEdge 512`, for timelines and lists (CP-05).
- **Records the result on the `Media` item** (`GARDEN#{id}/MEDIA#{media_id}`, a conditional update so
  a missing record is never created): `processing_status` (`pending` → `ready` | `failed`),
  `detected_format`, `width`, `height`, `bytes`, `analysis_s3_key`, `thumb_s3_key`, and
  `processing_error` on failure ("larger than 10 MB", "not an image", "corrupt", "unsupported
  format").
- **Failure handling:** S3's asynchronous invoke retries twice, then an SQS failure destination keeps
  the event. Re-processing is idempotent (same derived keys).
- **Packaging/IAM:** its own folder `app/ingestion/` (container image, ARM64, ADR-0014). Role:
  `s3:GetObject` on `uploads/*`, `s3:PutObject` on `derived/*` (both only to sign the URLs it hands
  the tool), `dynamodb:UpdateItem` on the app table, `lambda:InvokeFunction` on the Image Tool only.
  No model access.

### 3. Upload limit: 10 MB

- `createMediaUpload` requires the file size (`sizeBytes`) and rejects anything over 10 MB with
  `400` before signing. The frontend checks `file.size` first and tells the user ("Photos up to
  10 MB").
- A presigned PUT cannot enforce a size, so the Ingestion Lambda (via the tool's 10 MB source limit)
  is the backstop: an oversized object is marked `failed` and never analyzed.
- `createMediaUpload` writes `processing_status: pending` with the record.

### 4. Models only ever see the analysis copy

- `_resolve_image` uses `analysis_s3_key` with format `jpeg` when `processing_status` is `ready`.
- **Race with goal submission:** if the status is still `pending`, the orchestrator polls the `Media`
  item for up to 20 s. If it is still not ready, or `failed`, the turn proceeds **without** the photo
  and the reply says so ("I couldn't read that photo, could you resend it?"). A bad photo never
  reaches a specialist again, so it can no longer take every specialist down with it.
- Media uploaded before this change fall back to today's behaviour. A one-off backfill
  (`scripts/backfill_media.py`) copies each old original under `uploads/`, so it is processed through
  the normal trigger with no extra permissions (the ingestion role can only read `uploads/*`).

### 5. Every intake path shares it

The app upload, the CP-04 migration (which uploads originals under `uploads/` with the photo date
taken from the filename) and future WhatsApp intake all go through the Ingestion Lambda and the
Image Tool, so normalization rules live in one place.

### 6. Display uses the processed files

`getGoalDetail`, `listPlants` and later the plant timeline (CP-05) return presigned URLs for the
thumbnail or analysis copy where they exist (smaller, faster, and without GPS metadata), and the
original only where full resolution is asked for.

## Options Considered

| Option | Reusable | Covers app + migration + WhatsApp | Keeps original | Verdict |
|---|---|---|---|---|
| **A. Image Tool API + Ingestion Lambda on upload (chosen)** | Yes: any component or agent calls the tool | Yes | Yes | **Chosen** |
| B. Processing code inside the Ingestion Lambda only | No: the monorepo has no cross-package imports, so the migration script and agents would duplicate it | Yes | Yes | Rejected (owner asked for a reusable tool) |
| C. Normalize inside the orchestrator at analysis time | No | Yes | Yes | Rejected: repeated every turn, adds latency, needs S3 write on the orchestrator |
| D. Resize in the browser before upload | No | No | No, unless both are uploaded | Rejected as the primary control; may be added later to speed up uploads |
| E. Resize in each specialist (shared template) | Partly | Yes | Yes | Rejected: the same photo processed once per specialist per turn |

## Trade-off Analysis

A costs two small deployable units (the tool and the ingestion function), one extra hop per upload,
and a short `pending` window handled by the bounded poll. In exchange, image processing exists once
and can be reused by every component and agent; every model call gets a valid, right-sized JPEG; the
UI gets thumbnails (needed for CP-05 at 105 plants × many photos); GPS metadata stops leaking
through display URLs; and the migration needs no image code of its own. Keeping the tool stateless
(presigned URLs in and out) keeps it inside ADR-0013's boundary with no data permissions of its own.

## Consequences

- **Easier:** analysis stops failing on mislabelled or large photos; one reusable resize capability;
  thumbnails for timelines; a 10 MB upload limit users can see; the original is kept untouched.
- **Harder:** two new units plus an S3 notification and SQS failure destination to operate; new
  media keys (`uploads/`, `derived/`) alongside the old flat layout for existing objects; a `pending`
  state to handle in the orchestrator and UI.
- **Contract:** `createMediaUpload` request gains a required `sizeBytes` (400 above 10 MB); its
  response is unchanged. `Media` gains processing fields; photo URLs may point at derived files. The
  Image Tool has its own contract (`app/tools/image/openapi.yaml`).
- **ADR-0013:** respected: the tool owns no data; the Ingestion Lambda is the already-planned
  data-owning component; specialists still get only presigned URLs.
- **ADR-0012 / AF-03:** the tool is discovered like any other; no specialist gets it until its
  registry entry declares it.
- **ADR-0014:** both new functions are container images.

## Action Items

Built 2026-11-23 (sizing per tool via `app/tools/<name>/tool.json`, read by `AgentCoreStack`'s
tool loop; the S3 trigger attaches to the existing bucket in CDK's unmanaged mode, so notifications
configured outside CDK are preserved).


1. [x] Human review: approved 2026-11-23.
2. [x] `app/tools/image/`: `inspect` + `resize` (multi-output, presigned-URL or base64 I/O), contract,
   unit tests with PNG-as-.jpg, HEIC, over-10 MB, corrupt and decompression-bomb fixtures.
3. [x] `app/ingestion/`: S3-event handler calling the tool, Media update, tests.
4. [x] CDK: tool (via the existing tool loop), ingestion Lambda + S3 notification (prefix `uploads/`)
   + SQS failure destination + scoped IAM; assertion tests (trigger prefix, no recursion, the tool
   role has no S3/DynamoDB permission).
5. [x] Client API: `uploads/` keys, required `sizeBytes` (≤ 10 MB), `processing_status: pending`;
   read paths prefer derived URLs. Frontend: 10 MB check with a clear message.
6. [x] Orchestrator: analysis copy in `_resolve_image`; bounded wait; graceful "couldn't read the
   photo" reply.
7. [x] One-off backfill script (`scripts/backfill_media.py`, dry run by default). [ ] Run it in dev
   after deploy. CP-04 will upload under `uploads/`.
8. [x] Docs: `data-architecture.md` (Media fields, Ingestion implemented, Image tool in §6.1),
   `architecture.md` §3/§4.2.
9. [ ] Dev verification (pending deploy): upload the 4.6 MB PNG-as-.jpg photo; vision and agronomy both succeed;
   an 11 MB file is refused with a clear message.
