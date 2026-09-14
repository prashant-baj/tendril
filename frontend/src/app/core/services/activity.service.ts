import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, of } from 'rxjs';
import { CLIENT_API_BASE_URL } from '../config/client-api.config';
import { ActivityEvent } from '../models/activity.model';

/**
 * The garden's event timeline (`EVENT` entity, architecture.md §2 — "the capture-first log
 * that feeds the future data flywheel"). Shaped after `GET /gardens/{id}/activity` (Phase 6)
 * so a real implementation can replace `MockActivityApi` — done, see `HttpActivityApi` below.
 */
export abstract class ActivityApi {
  abstract getActivity(gardenId: string | null): Observable<ActivityEvent[]>;
}

@Injectable({ providedIn: 'root' })
export class MockActivityApi extends ActivityApi {
  getActivity(_gardenId: string | null): Observable<ActivityEvent[]> {
    return of([]);
  }
}

@Injectable({ providedIn: 'root' })
export class HttpActivityApi extends ActivityApi {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(CLIENT_API_BASE_URL);
  private readonly mock = new MockActivityApi();

  getActivity(gardenId: string | null): Observable<ActivityEvent[]> {
    if (!gardenId) {
      return this.mock.getActivity(gardenId);
    }
    return this.http.get<ActivityEvent[]>(`${this.baseUrl}/gardens/${gardenId}/activity`);
  }
}
