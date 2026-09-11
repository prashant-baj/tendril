export type GateTone = 'neutral' | 'weather' | 'pest';

/** One task in a Goal's approved Plan (see PLAN/TASK in architecture.md §2). */
export interface PlanTask {
  whenTop: string;
  whenBig: string;
  title: string;
  detail: string;
  scope: string;
  scopeIcon: string;
  source: string;
  gate: string;
  gateIcon: string;
  gateTone: GateTone;
}

export interface FollowUp {
  icon: string;
  what: string;
  when: string;
}

/** A diagnosis surfaced on the capture/diagnose screen once analysis completes. */
export interface Finding {
  title: string;
  detail: string;
  icon: string;
  confidence: string;
}
