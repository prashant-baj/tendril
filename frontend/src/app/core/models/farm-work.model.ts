import { PlantPhoto } from './plant.model';

/** ADR-0038: counted from the last time the work was done. Absent = one-off. */
export interface Schedule {
  everyDays?: number;
  /** A window, e.g. 21..28 for "every 3-4 weeks". */
  minDays?: number;
  maxDays?: number;
}

export type ActivityKind = 'input' | 'operation';
export type Verdict = 'beneficial' | 'caution' | 'harmful' | 'unknown';
export type DueState = 'overdue' | 'due' | 'upcoming' | 'none' | 'paused' | 'done';
/** `"all"` (garden-wide) or the plant ids the work is for. */
export type Targets = 'all' | string[];

/** The Supervisor's view of a piece of farm work (ADR-0038 §4, ADR-0039). Advisory only. */
export interface Assessment {
  verdict: Verdict;
  summary: string;
  benefits: string[];
  risks: string[];
  conflicts: { with: string; reason: string }[];
  suggestion?: string;
  assessedAt?: string;
  /** The work changed after it was assessed; a fresh assessment is on its way. */
  stale?: boolean;
}

/** `Activity` in app/api/openapi.yaml: planned work, one-off or recurring. */
export interface Activity {
  activityId: string;
  title: string;
  kind: ActivityKind;
  details?: string;
  product?: string;
  dose?: string;
  targets: Targets;
  schedule?: Schedule;
  status: 'active' | 'paused' | 'done' | 'archived';
  dueState: DueState;
  startOn?: string;
  nextDueOn?: string;
  overdueOn?: string;
  lastDoneOn?: string;
  source: 'user' | 'confirmed';
  assessment?: Assessment;
}

/** `ActivityLog`: work that was done. */
export interface ActivityLog {
  logId: string;
  activityId?: string;
  title: string;
  kind: ActivityKind;
  details?: string;
  product?: string;
  dose?: string;
  targets: Targets;
  doneOn: string;
  note?: string;
  media: PlantPhoto[];
  assessment?: Assessment;
}

export interface ActivityLogPage {
  logs: ActivityLog[];
  nextCursor?: string;
}

/** A Supervisor suggestion; only Confirm applies it (ADR-0039 §5). */
export interface GardenAction {
  actionId: string;
  type: 'create_activity' | 'set_schedule' | 'move_due' | 'pause_activity' | 'resume_activity';
  summary: string;
  reason?: string;
  status: 'proposed' | 'confirmed' | 'rejected' | 'superseded';
  activityId?: string;
  createdAt: string;
}

export interface ActivityRequest {
  title?: string;
  kind?: ActivityKind;
  details?: string;
  product?: string;
  dose?: string;
  targets?: Targets;
  schedule?: Schedule | null;
  startOn?: string;
  status?: 'active' | 'paused' | 'archived';
}

export interface ActivityLogRequest {
  activityId?: string;
  title?: string;
  kind?: ActivityKind;
  details?: string;
  product?: string;
  dose?: string;
  targets?: Targets;
  doneOn?: string;
  note?: string;
  mediaIds?: string[];
}
