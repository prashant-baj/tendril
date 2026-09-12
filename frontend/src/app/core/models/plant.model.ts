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
}

/** Client API request body for `POST /gardens/{gardenId}/plants` (OB-02, app/api/openapi.yaml). */
export interface CreatePlantRequest {
  species: string;
  variety?: string;
  /** Id returned by a prior `requestMediaUpload()` call, once the upload itself completed. */
  mediaId?: string;
}
