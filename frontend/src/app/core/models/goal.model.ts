export type GoalStatus = 'in-progress' | 'completed';

/** Mirrors GOAL + PLAN in docs/architecture/architecture.md §2, plus UI presentation. */
export interface Goal {
  goalId: string;
  title: string;
  garden: string;
  status: GoalStatus;
  statusLabel: string;
  statusIcon: string;
  timing: string;
  next: string;
  /** 0-100. */
  progressPct: number;
  successCriteria: string;
}

export interface GoalStat {
  value: string;
  label: string;
}

/** Client API request for `POST /gardens/{gardenId}/goals` (WS-03/WS-05). */
export interface CreateGoalRequest {
  description: string;
  /** Ids from prior requestMediaUpload calls, once each upload has completed. */
  mediaIds?: string[];
}
