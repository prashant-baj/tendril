/** One task in a Goal's proposed/approved Plan (PA-01, data-architecture.md §2). */
export interface Task {
  taskId: string;
  title: string;
  detail: string;
  /** "plant" or "garden". */
  scope: string;
  status: string;
  /** Inherited from the Goal's plantId, if it has one. */
  plantId?: string;
  /** Set once a check-in photo has been attached (postTaskCheckin). */
  media?: { mediaId: string; downloadUrl: string };
  /** The orchestrator's assessment of this task's check-in photo against the plan (PA-05) —
   * written asynchronously, may take a few seconds to appear after check-in. */
  feedback?: string;
}

/** One specialist's (or the orchestrator's own) contribution to a Plan's "How this was decided"
 * trace (PA-05). */
export interface SpecialistTraceEntry {
  agent: string;
  says: string;
  ms: number;
  isOrchestrator?: boolean;
}

/** A Goal's proposed/approved Plan (PA-01). 1:1 with its Goal — planId === goalId. */
export interface Plan {
  planId: string;
  goalId: string;
  successCriteria: string;
  /** "PlanProposed" or "Approved" (architecture.md §7.3). */
  status: string;
  /** The specialist-consultation record behind this plan's current version (PA-05). Absent for
   * an older goal, or a plan synthesized from a fallback with no specialist actually consulted. */
  trace?: SpecialistTraceEntry[];
}
