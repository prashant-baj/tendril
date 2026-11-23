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
