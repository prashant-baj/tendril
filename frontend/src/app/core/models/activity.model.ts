/** Presentation tone for an activity timeline entry's node. */
export type ActivityTone = 'neutral' | 'adapt' | 'goal';

/**
 * One entry in a garden's event timeline (`EVENT` entity, architecture.md §2, implemented in
 * Phase 6). Deliberately close to the raw API shape — `type`/`payload` are server-owned and
 * UI-agnostic; presentation (title/detail/icon/tone) is derived client-side via
 * `activity-presentation.util.ts`, mirroring `specialist-icon.util.ts`'s pattern.
 */
export interface ActivityEvent {
  type: string;
  payload: Record<string, string>;
  createdAt: string;
  /** Present whenever the event's payload carries one — lets an entry link back to its goal. */
  goalId?: string;
}
