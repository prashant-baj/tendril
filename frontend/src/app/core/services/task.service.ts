import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, of } from 'rxjs';
import { CLIENT_API_BASE_URL } from '../config/client-api.config';
import { GardenTaskItem } from '../models/task.model';

/**
 * Every task across every goal in a garden (Phase 6, `GET /gardens/{id}/tasks`) — real tasks
 * have no due-date/schedule concept yet (that's Phase 7+ tracker work), so this is a flat,
 * ungrouped list; grouping into "To do"/"Done" happens client-side (`TasksComponent`).
 */
export abstract class TaskApi {
  abstract getTasks(gardenId: string | null): Observable<GardenTaskItem[]>;
}

@Injectable({ providedIn: 'root' })
export class MockTaskApi extends TaskApi {
  getTasks(_gardenId: string | null): Observable<GardenTaskItem[]> {
    return of([]);
  }
}

@Injectable({ providedIn: 'root' })
export class HttpTaskApi extends TaskApi {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(CLIENT_API_BASE_URL);
  private readonly mock = new MockTaskApi();

  getTasks(gardenId: string | null): Observable<GardenTaskItem[]> {
    if (!gardenId) {
      return this.mock.getTasks(gardenId);
    }
    return this.http.get<GardenTaskItem[]>(`${this.baseUrl}/gardens/${gardenId}/tasks`);
  }
}
