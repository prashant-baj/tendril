import { ActivityEvent, ActivityTone } from '../../core/models/activity.model';

export interface ActivityPresentation {
  title: string;
  detail: string;
  icon: string;
  tone: ActivityTone;
}

/** Maps a raw Event's `type`/`payload` (Phase 6, UI-agnostic by design) to how the Activity
 * timeline actually renders it — mirrors `specialist-icon.util.ts`'s "keep the backend free of
 * UI concerns" pattern. */
export function activityPresentation(event: ActivityEvent): ActivityPresentation {
  switch (event.type) {
    case 'goal.submitted':
      return {
        title: 'New issue reported',
        detail: event.payload['description'] ?? '',
        icon: 'flag',
        tone: 'goal',
      };
    case 'plan.updated':
      return {
        title: 'Plan updated',
        detail: event.payload['successCriteria']
          ? `Done when: ${event.payload['successCriteria']}`
          : '',
        icon: 'auto_awesome',
        tone: 'adapt',
      };
    case 'plan.approved':
      return {
        title: 'Plan approved',
        detail: 'You approved the plan.',
        icon: 'check_circle',
        tone: 'goal',
      };
    case 'task.checkin':
      return {
        title: 'Task checked in',
        detail: event.payload['taskTitle'] ? `"${event.payload['taskTitle']}"` : '',
        icon: 'photo_camera',
        tone: 'neutral',
      };
    default:
      return { title: event.type, detail: '', icon: 'info', tone: 'neutral' };
  }
}
