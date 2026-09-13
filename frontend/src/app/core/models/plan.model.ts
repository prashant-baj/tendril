/** One task in a Goal's proposed/approved Plan (PA-01, data-architecture.md §2). */
export interface Task {
  taskId: string;
  title: string;
  detail: string;
  /** "plant" or "garden". */
  scope: string;
  status: string;
}

/** A Goal's proposed/approved Plan (PA-01). 1:1 with its Goal — planId === goalId. */
export interface Plan {
  planId: string;
  goalId: string;
  successCriteria: string;
  /** "PlanProposed" or "Approved" (architecture.md §7.3). */
  status: string;
}
