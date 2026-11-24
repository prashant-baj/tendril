import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { CLIENT_API_BASE_URL } from '../config/client-api.config';
import {
  Activity,
  ActivityLog,
  ActivityLogPage,
  ActivityLogRequest,
  ActivityRequest,
  GardenAction,
} from '../models/farm-work.model';

/**
 * Farm work (ADR-0038) and the Supervisor's suggestions (ADR-0039) — `/gardens/{id}/activities`,
 * `/activity-logs` and `/actions` in app/api/openapi.yaml. (Not `ActivityApi`, which is the
 * Activity screen's event feed.)
 */
@Injectable({ providedIn: 'root' })
export class FarmWorkApi {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(CLIENT_API_BASE_URL);

  private url(gardenId: string, path: string): string {
    return `${this.baseUrl}/gardens/${gardenId}/${path}`;
  }

  listActivities(gardenId: string): Observable<Activity[]> {
    return this.http.get<Activity[]>(this.url(gardenId, 'activities'));
  }

  createActivity(gardenId: string, request: ActivityRequest): Observable<Activity> {
    return this.http.post<Activity>(this.url(gardenId, 'activities'), request);
  }

  updateActivity(gardenId: string, activityId: string, request: ActivityRequest): Observable<Activity> {
    return this.http.patch<Activity>(this.url(gardenId, `activities/${activityId}`), request);
  }

  logActivity(gardenId: string, request: ActivityLogRequest): Observable<ActivityLog> {
    return this.http.post<ActivityLog>(this.url(gardenId, 'activity-logs'), request);
  }

  listLogs(gardenId: string, cursor?: string): Observable<ActivityLogPage> {
    const params: Record<string, string> = cursor ? { cursor } : {};
    return this.http.get<ActivityLogPage>(this.url(gardenId, 'activity-logs'), { params });
  }

  listSuggestions(gardenId: string): Observable<GardenAction[]> {
    return this.http.get<GardenAction[]>(this.url(gardenId, 'actions'));
  }

  confirmSuggestion(gardenId: string, actionId: string): Observable<void> {
    return this.http.post<void>(this.url(gardenId, `actions/${actionId}/confirm`), {});
  }

  rejectSuggestion(gardenId: string, actionId: string): Observable<void> {
    return this.http.post<void>(this.url(gardenId, `actions/${actionId}/reject`), {});
  }
}
