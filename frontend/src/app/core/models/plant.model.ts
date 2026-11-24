export type PlantHealthState = 'healthy' | 'needs-care' | 'watch';

/** Mirrors the PLANT entity in docs/architecture/architecture.md §2, plus UI presentation. */
export interface Plant {
  plantId: string;
  name: string;
  species: string;
  variety: string;
  stage: string;
  icon: string;
  healthState: PlantHealthState;
  /** Short summary line shown on the garden screen, e.g. "Pusa Ruby · 64 days · 12 L pot". */
  meta: string;
  /** Freshly-generated presigned GET url (OB-03), present only if the plant has a linked photo. */
  photoUrl?: string;
  /** Number on the plant's physical stake, when it has one (ADR-0032). Unique within a garden. */
  stakeNo?: number;
  /** Status of the newest history entry that set one (ADR-0035). */
  latestStatus?: PlantStatus;
  /** Date of the newest history entry (YYYY-MM-DD). */
  latestObservedOn?: string;
  historyCount?: number;
}

/** Client API request body for `POST /gardens/{gardenId}/plants` (OB-02, app/api/openapi.yaml). */
export interface CreatePlantRequest {
  species: string;
  variety?: string;
  /** Id returned by a prior `requestMediaUpload()` call, once the upload itself completed. */
  mediaId?: string;
  /** Growth stage; the server defaults it to "new". */
  stage?: string;
  /** Optional stake number (ADR-0032) — a 409 means it's already used in this garden. */
  stakeNo?: number;
}

/** `POST /gardens/{gardenId}/plants` response. */
export interface CreatePlantResponse {
  plantId: string;
  photoUrl?: string;
  stakeNo?: number;
  /** True when `stakeNo` is outside the garden's stake range; the plant is still created. */
  outOfRange?: boolean;
}

/** Client API request body for `POST /gardens/{gardenId}/plants/bulk` (ADR-0032). */
export interface BulkCreatePlantsRequest {
  species: string;
  variety?: string;
  stage?: string;
  stakeFrom: number;
  /** Inclusive; at most 200 plants per call. */
  stakeTo: number;
}

/** `POST /gardens/{gardenId}/plants/bulk` response. Idempotent: existing numbers are skipped. */
export interface BulkCreatePlantsResponse {
  created: { plantId: string; stakeNo: number; outOfRange?: boolean }[];
  skipped: number[];
}

/** A plant-history status (ADR-0035), the owner's tracker scale: healthy > ok > watch. */
export type PlantStatus = 'healthy' | 'ok' | 'watch';

/** A photo in a plant's history: presigned thumbnail + analysis-copy urls, fresh per read. */
export interface PlantPhoto {
  mediaId: string;
  /** For a video (ADR-0038 §7): `thumbUrl` is its poster frame once processed, `url` plays it. */
  kind?: 'photo' | 'video';
  thumbUrl: string;
  url: string;
}

/** One dated entry in a plant's history (`PlantLogEntry`, app/api/openapi.yaml). */
export interface PlantLogEntry {
  entryId: string;
  /** YYYY-MM-DD — when it was observed (the photo's own date when known), not uploaded. */
  observedOn: string;
  observedAt?: string;
  kind: 'photo' | 'checkin' | 'log' | 'retraction';
  /** `tendril`: Tendril's own note for a saved goal (ADR-0037) — never sets a status. */
  source: 'user' | 'confirmed' | 'auto' | 'migration' | 'tendril';
  status?: PlantStatus;
  note?: string;
  photos: PlantPhoto[];
  goalId?: string;
  taskId?: string;
  retracts?: string;
}

/** `GET /gardens/{gardenId}/plants/{plantId}` (CP-05, ADR-0035 §6). */
export interface PlantDetail {
  plant: Plant;
  history: { entries: PlantLogEntry[]; nextCursor?: string };
}

export interface PlantHistoryQuery {
  limit?: number;
  cursor?: string;
  order?: 'newest' | 'oldest';
}

/** `POST /gardens/{gardenId}/plants/{plantId}/history` — at least one of note/status/mediaIds. */
export interface PlantLogRequest {
  note?: string;
  status?: PlantStatus;
  mediaIds?: string[];
  /** YYYY-MM-DD, not in the future; defaults to the photo's date, else today. */
  observedOn?: string;
}
