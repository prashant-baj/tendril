export type ActivityTone = 'neutral' | 'adapt' | 'goal';

/** Mirrors the EVENT entity in docs/architecture/architecture.md §2, plus UI presentation. */
export interface ActivityEvent {
  when: string;
  title: string;
  detail: string;
  icon: string;
  tone: ActivityTone;
  quote?: string;
}
