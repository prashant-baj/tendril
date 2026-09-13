/** Mirrors the GARDEN entity in docs/architecture/architecture.md §2. */
export interface Garden {
  gardenId: string;
  name: string;
  vision: string;
  geolocation: string;
  climateZone: string;
  /** Freshly-generated presigned GET url for this garden's banner photo, if one was set. */
  photoUrl?: string;
}

/** A small labelled fact chip (sun hours, container type, etc.). */
export interface GardenFact {
  icon: string;
  label: string;
}

/** Client API request body for `POST /gardens` (OB-01, app/api/openapi.yaml). */
export interface CreateGardenRequest {
  name: string;
  geolocation: string;
  vision?: string;
  /** Client-supplied id, used only when a photo was uploaded first (that upload needs an
   * existing gardenId) — generated via `core/utils/id.util.ts`. Omit to let the server generate
   * one, as for every garden without a photo. */
  gardenId?: string;
  /** Id from a prior requestMediaUpload call (uploaded under the same gardenId above). */
  mediaId?: string;
}

/** One entry in the multi-garden switcher (`GET /gardens`) — just enough to list and switch,
 * not the full `Garden` record. */
export interface GardenSummary {
  gardenId: string;
  name: string;
}

/** `GET /gardens/{id}/weather` — deliberately raw (no icon/label baked in server-side); see
 * `shared/components/weather-presentation.util.ts` for the presentation mapping. */
export interface GardenWeather {
  temperatureC: number;
  weatherCode: number;
}
