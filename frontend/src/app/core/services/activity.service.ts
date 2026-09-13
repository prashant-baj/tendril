import { Injectable } from '@angular/core';
import { Observable, of } from 'rxjs';
import { ActivityEvent } from '../models/activity.model';

/**
 * The garden's event timeline (EVENT entity, architecture.md §2 — "the capture-first log
 * that feeds the future data flywheel"). Shaped after `GET /gardens/{id}/status` (ADR-0004)
 * so a real implementation can replace `MockActivityApi` later.
 */
export abstract class ActivityApi {
  abstract getActivity(): Observable<ActivityEvent[]>;
}

@Injectable({ providedIn: 'root' })
export class MockActivityApi extends ActivityApi {
  // No event/status read endpoint exists yet (ADR-0004 defines `GET /gardens/{id}/status`;
  // not implemented) — blank until that backend work is done.
  private readonly activity: ActivityEvent[] = [];

  getActivity(): Observable<ActivityEvent[]> {
    return of(this.activity);
  }
}
