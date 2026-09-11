import { Injectable } from '@angular/core';
import { Observable, of } from 'rxjs';
import { Garden, GardenFact } from '../models/garden.model';
import { Plant } from '../models/plant.model';

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
}
