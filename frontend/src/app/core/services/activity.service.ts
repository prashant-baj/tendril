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
  private readonly activity: ActivityEvent[] = [
    {
      when: 'Today · 6:40 AM',
      title: 'Reminder sent on WhatsApp',
      detail: 'Hand-pollination reminder delivered to +91 98•• ••42.',
      icon: 'chat',
      tone: 'neutral',
      quote: 'Good morning Meera — tap the tomato flowers gently before 8 AM. Reply with a photo when you’re done.',
    },
    {
      when: 'Yesterday · 7:15 PM',
      title: 'You added 2 photos',
      detail: 'Tomato #2, lower leaves and flower truss.',
      icon: 'photo_camera',
      tone: 'neutral',
    },
    {
      when: 'Tue · 9:02 AM',
      title: 'Plan adapted',
      detail: 'The heat spell broke early, so watering moved back to alternate mornings and the mulch task was pulled forward a day.',
      icon: 'autorenew',
      tone: 'adapt',
    },
    {
      when: 'Mon · 8:30 AM',
      title: 'Goal created from a photo',
      detail: '6 specialists consulted. 4 tasks proposed over 18 days, approved by you the same morning.',
      icon: 'flag',
      tone: 'goal',
    },
    {
      when: '2 Sep',
      title: 'Goal completed: black spot cleared',
      detail: 'No recurrence for 14 days. Kept in memory for next monsoon.',
      icon: 'check_circle',
      tone: 'goal',
    },
  ];

  getActivity(): Observable<ActivityEvent[]> {
    return of(this.activity);
  }
}
