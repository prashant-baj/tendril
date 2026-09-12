import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Observable, combineLatest, of } from 'rxjs';
import { map, tap } from 'rxjs/operators';
import { CLIENT_API_BASE_URL } from '../config/client-api.config';
import { CreateGardenRequest, Garden, GardenFact } from '../models/garden.model';
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
  /** Short list for the Home screen's horizontally-scrolling plant strip. */
  abstract getPlantsSummary(): Observable<Plant[]>;
  /** Full list for the Garden screen. */
  abstract getPlants(): Observable<Plant[]>;
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
  /** OB-02: `POST /gardens/{gardenId}/plants`. */
  abstract createPlant(
    gardenId: string,
    request: CreatePlantRequest,
  ): Observable<{ plantId: string }>;
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

  private readonly gardenFacts: GardenFact[] = [
    { icon: 'location_on', label: 'Pune, 18.52°N' },
    { icon: 'sunny', label: '~5 hrs direct sun' },
    { icon: 'yard', label: 'Containers only' },
    { icon: 'calendar_month', label: 'Kharif, week 11' },
  ];

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

  getPlantsSummary(): Observable<Plant[]> {
    return of(this.plantsSummary);
  }

  getPlants(): Observable<Plant[]> {
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
}

interface GardenDto {
  gardenId: string;
  name: string;
  geolocation: string;
  vision?: string;
  ownerUserId: string;
  createdAt: string;
}

/**
 * Real Client API implementation of `createGarden`/`getGardenById` (OB-01). Every other method
 * still has no backend yet, so it delegates to an internal `MockGardenApi` — swapping the
 * `app.config.ts` binding to this class doesn't regress the still-mocked screens.
 */
@Injectable({ providedIn: 'root' })
export class HttpGardenApi extends GardenApi {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(CLIENT_API_BASE_URL);
  private readonly mock = new MockGardenApi();

  // OB-02: there's no GET-list-plants operation yet (not in openapi.yaml — only create), so
  // "reflects real data immediately after adding one" is done by prepending each created plant
  // onto the still-mocked base list client-side, rather than re-fetching from a real endpoint
  // that doesn't exist. A real list read is a later story once one is actually needed.
  private readonly addedPlants = new BehaviorSubject<Plant[]>([]);

  getGarden(): Observable<Garden> {
    return this.mock.getGarden();
  }

  getGardenFacts(): Observable<GardenFact[]> {
    return this.mock.getGardenFacts();
  }

  getPlantsSummary(): Observable<Plant[]> {
    return combineLatest([this.mock.getPlantsSummary(), this.addedPlants]).pipe(
      map(([base, added]) => [...added, ...base]),
    );
  }

  getPlants(): Observable<Plant[]> {
    return combineLatest([this.mock.getPlants(), this.addedPlants]).pipe(
      map(([base, added]) => [...added, ...base]),
    );
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

  createPlant(gardenId: string, request: CreatePlantRequest): Observable<{ plantId: string }> {
    return this.http
      .post<{ plantId: string }>(`${this.baseUrl}/gardens/${gardenId}/plants`, request)
      .pipe(
        tap(({ plantId }) => {
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
          this.addedPlants.next([plant, ...this.addedPlants.value]);
        }),
      );
  }
}
