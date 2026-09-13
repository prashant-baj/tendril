import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, of } from 'rxjs';
import { map } from 'rxjs/operators';
import { CLIENT_API_BASE_URL } from '../config/client-api.config';
import { CreateGardenRequest, Garden, GardenFact } from '../models/garden.model';
import { CreateGoalRequest } from '../models/goal.model';
import { CreatePlantRequest, Plant } from '../models/plant.model';
import { MediaUploadRequest, MediaUploadResponse } from '../models/media.model';

/**
 * Garden + plant read model. Shaped after the Client API in ADR-0004 / architecture.md §4.1
 * (`GET /gardens/{id}`, `GET /gardens/{id}/plants`) so a real `HttpGardenApi` can replace
 * `MockGardenApi` later without touching any component.
 */
export abstract class GardenApi {
  abstract getGarden(): Observable<Garden>;
  abstract getGardenFacts(): Observable<GardenFact[]>;
  /**
   * Short list for the Home screen's horizontally-scrolling plant strip, and the full list for
   * the Garden screen. `gardenId` is `null` for the pre-onboarding demo state (no garden created
   * yet) — implementations fall back to fixture data in that case, matching
   * `CurrentGardenService.garden$`'s existing pre-onboarding fallback.
   */
  abstract getPlantsSummary(gardenId: string | null): Observable<Plant[]>;
  abstract getPlants(gardenId: string | null): Observable<Plant[]>;
  /** OB-01: `POST /gardens`. */
  abstract createGarden(request: CreateGardenRequest): Observable<{ gardenId: string }>;
  /** OB-01: `GET /gardens/{gardenId}`. */
  abstract getGardenById(gardenId: string): Observable<Garden>;
  /** OB-02: `POST /gardens/{gardenId}/media` — returns a presigned S3 PUT URL + its mediaId. */
  abstract requestMediaUpload(
    gardenId: string,
    request: MediaUploadRequest,
  ): Observable<MediaUploadResponse>;
  /** OB-02: uploads the file bytes directly to S3 using a presigned URL — never via the Lambda. */
  abstract uploadMedia(uploadUrl: string, file: File): Observable<void>;
  /** OB-02: `POST /gardens/{gardenId}/plants`. `photoUrl` (OB-03) is present only if `mediaId`
   * was given. */
  abstract createPlant(
    gardenId: string,
    request: CreatePlantRequest,
  ): Observable<{ plantId: string; photoUrl?: string }>;
  /** Plant lifecycle: `DELETE /gardens/{gardenId}/plants/{plantId}`. */
  abstract deletePlant(gardenId: string, plantId: string): Observable<void>;
  /** WS-03/WS-05: `POST /gardens/{gardenId}/goals` — 202, async orchestration (ADR-0012). */
  abstract createGoal(
    gardenId: string,
    request: CreateGoalRequest,
  ): Observable<{ goalId: string; status: string }>;
}

@Injectable({ providedIn: 'root' })
export class MockGardenApi extends GardenApi {
  private readonly garden: Garden = {
    gardenId: 'balcony-kitchen-garden',
    name: 'Balcony Kitchen Garden',
    vision: 'Fresh organic veggies from my balcony.',
    geolocation: 'Pune, 18.52°N',
    climateZone: 'Kharif, week 11',
  };

  // No garden-facts read endpoint exists yet — blank until that backend work is done.
  private readonly gardenFacts: GardenFact[] = [];

  private readonly plantsSummary: Plant[] = [
    { plantId: 'tomato-2', name: 'Tomato #2', species: 'Tomato', variety: 'Pusa Ruby', stage: 'fruiting', icon: 'potted_plant', healthState: 'needs-care', meta: '' },
    { plantId: 'chilli-1', name: 'Chilli #1', species: 'Chilli', variety: 'Jwala', stage: 'growing', icon: 'local_florist', healthState: 'healthy', meta: '' },
    { plantId: 'spinach-bed', name: 'Spinach bed', species: 'Spinach', variety: 'All Green', stage: 'growing', icon: 'grass', healthState: 'healthy', meta: '' },
    { plantId: 'mint-box', name: 'Mint box', species: 'Mint', variety: '', stage: 'growing', icon: 'eco', healthState: 'healthy', meta: '' },
  ];

  // Note: the mockup's "9 plants" heading is a static mock label — only 5 rows exist in the
  // fixture data. Ported as-is (this is demo data, not a real inventory count).
  private readonly plants: Plant[] = [
    { plantId: 'tomato-2', name: 'Tomato #2', species: 'Tomato', variety: 'Pusa Ruby', stage: 'fruiting', icon: 'potted_plant', healthState: 'needs-care', meta: 'Pusa Ruby · 64 days · 12 L pot' },
    { plantId: 'tomato-1', name: 'Tomato #1', species: 'Tomato', variety: 'Pusa Ruby', stage: 'fruiting', icon: 'potted_plant', healthState: 'healthy', meta: 'Pusa Ruby · 64 days · 12 L pot' },
    { plantId: 'chilli-1', name: 'Chilli #1', species: 'Chilli', variety: 'Jwala', stage: 'growing', icon: 'local_florist', healthState: 'healthy', meta: 'Jwala · 41 days · 8 L pot' },
    { plantId: 'spinach-bed', name: 'Spinach bed', species: 'Spinach', variety: 'All Green', stage: 'growing', icon: 'grass', healthState: 'healthy', meta: 'All Green · 22 days · window box' },
    { plantId: 'mint-box', name: 'Mint box', species: 'Mint', variety: '', stage: 'growing', icon: 'eco', healthState: 'watch', meta: 'Sown 3 Aug · window box' },
  ];

  getGarden(): Observable<Garden> {
    return of(this.garden);
  }

  getGardenFacts(): Observable<GardenFact[]> {
    return of(this.gardenFacts);
  }

  getPlantsSummary(_gardenId: string | null): Observable<Plant[]> {
    return of(this.plantsSummary);
  }

  getPlants(_gardenId: string | null): Observable<Plant[]> {
    return of(this.plants);
  }

  // Keyed store so a garden created via createGarden() can be read back via getGardenById(),
  // seeded with the mock single-garden fixture above.
  private readonly createdGardens = new Map<string, Garden>([[this.garden.gardenId, this.garden]]);

  createGarden(request: CreateGardenRequest): Observable<{ gardenId: string }> {
    const gardenId = `mock-${Math.random().toString(36).slice(2, 10)}`;
    this.createdGardens.set(gardenId, {
      gardenId,
      name: request.name,
      vision: request.vision ?? '',
      geolocation: request.geolocation,
      climateZone: '',
    });
    return of({ gardenId });
  }

  getGardenById(gardenId: string): Observable<Garden> {
    return of(this.createdGardens.get(gardenId) ?? this.garden);
  }

  requestMediaUpload(
    _gardenId: string,
    _request: MediaUploadRequest,
  ): Observable<MediaUploadResponse> {
    return of({
      uploadUrl: 'about:blank',
      mediaId: `mock-media-${Math.random().toString(36).slice(2, 10)}`,
    });
  }

  uploadMedia(_uploadUrl: string, _file: File): Observable<void> {
    return of(undefined);
  }

  createPlant(_gardenId: string, request: CreatePlantRequest): Observable<{ plantId: string }> {
    const plantId = `mock-plant-${Math.random().toString(36).slice(2, 10)}`;
    const plant: Plant = {
      plantId,
      name: request.species,
      species: request.species,
      variety: request.variety ?? '',
      stage: 'new',
      icon: 'eco',
      healthState: 'healthy',
      meta: request.variety ?? '',
    };
    this.plants.unshift(plant);
    this.plantsSummary.unshift(plant);
    return of({ plantId });
  }

  deletePlant(_gardenId: string, plantId: string): Observable<void> {
    for (const list of [this.plants, this.plantsSummary]) {
      const index = list.findIndex((p) => p.plantId === plantId);
      if (index !== -1) {
        list.splice(index, 1);
      }
    }
    return of(undefined);
  }

  createGoal(
    _gardenId: string,
    _request: CreateGoalRequest,
  ): Observable<{ goalId: string; status: string }> {
    return of({ goalId: `mock-goal-${Math.random().toString(36).slice(2, 10)}`, status: 'Intake' });
  }
}

interface GardenDto {
  gardenId: string;
  name: string;
  geolocation: string;
  vision?: string;
  ownerUserId: string;
  createdAt: string;
}

/** Shape returned by `GET /gardens/{gardenId}/plants` (app/api/openapi.yaml's `Plant` schema). */
interface PlantDto {
  plantId: string;
  species: string;
  variety?: string;
  stage: string;
  /** Freshly-generated presigned GET url (OB-03), present only if the plant has a linked photo. */
  photoUrl?: string;
}

function plantFromDto(dto: PlantDto): Plant {
  return {
    plantId: dto.plantId,
    name: dto.species,
    species: dto.species,
    variety: dto.variety ?? '',
    stage: dto.stage,
    // No real health-tracking backend yet — a generic icon/healthy state stands in for it until
    // that story lands. The photo itself is real (OB-03); `icon` is just its no-photo fallback.
    icon: 'eco',
    healthState: 'healthy',
    meta: dto.variety ?? '',
    photoUrl: dto.photoUrl,
  };
}

/**
 * Real Client API implementation of `createGarden`/`getGardenById` (OB-01) and the full Plant
 * lifecycle (create/list/delete). `getGarden`/`getGardenFacts` still have no backend (no story
 * has added `climateZone`/facts to the API yet) and delegate to an internal `MockGardenApi`.
 */
@Injectable({ providedIn: 'root' })
export class HttpGardenApi extends GardenApi {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(CLIENT_API_BASE_URL);
  private readonly mock = new MockGardenApi();

  getGarden(): Observable<Garden> {
    return this.mock.getGarden();
  }

  getGardenFacts(): Observable<GardenFact[]> {
    return this.mock.getGardenFacts();
  }

  getPlantsSummary(gardenId: string | null): Observable<Plant[]> {
    return this.getPlants(gardenId);
  }

  getPlants(gardenId: string | null): Observable<Plant[]> {
    // No garden created yet (pre-onboarding demo state) — nothing real to list.
    if (!gardenId) {
      return this.mock.getPlants(gardenId);
    }
    return this.http
      .get<PlantDto[]>(`${this.baseUrl}/gardens/${gardenId}/plants`)
      .pipe(map((dtos) => dtos.map(plantFromDto)));
  }

  createGarden(request: CreateGardenRequest): Observable<{ gardenId: string }> {
    return this.http.post<{ gardenId: string }>(`${this.baseUrl}/gardens`, request);
  }

  getGardenById(gardenId: string): Observable<Garden> {
    return this.http
      .get<GardenDto>(`${this.baseUrl}/gardens/${gardenId}`)
      .pipe(
        map((dto) => ({
          gardenId: dto.gardenId,
          name: dto.name,
          geolocation: dto.geolocation,
          vision: dto.vision ?? '',
          climateZone: '',
        })),
      );
  }

  requestMediaUpload(
    gardenId: string,
    request: MediaUploadRequest,
  ): Observable<MediaUploadResponse> {
    return this.http.post<MediaUploadResponse>(
      `${this.baseUrl}/gardens/${gardenId}/media`,
      request,
    );
  }

  uploadMedia(uploadUrl: string, file: File): Observable<void> {
    // The presigned URL's signature was computed over this exact Content-Type (garden_handler.py
    // passes it to generate_presigned_url) — sending a different one (or none) fails S3-side.
    return this.http
      .put<void>(uploadUrl, file, { headers: { 'Content-Type': file.type } })
      .pipe(map(() => undefined));
  }

  createPlant(
    gardenId: string,
    request: CreatePlantRequest,
  ): Observable<{ plantId: string; photoUrl?: string }> {
    return this.http.post<{ plantId: string; photoUrl?: string }>(
      `${this.baseUrl}/gardens/${gardenId}/plants`,
      request,
    );
  }

  deletePlant(gardenId: string, plantId: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/gardens/${gardenId}/plants/${plantId}`);
  }

  createGoal(
    gardenId: string,
    request: CreateGoalRequest,
  ): Observable<{ goalId: string; status: string }> {
    return this.http.post<{ goalId: string; status: string }>(
      `${this.baseUrl}/gardens/${gardenId}/goals`,
      request,
    );
  }
}
