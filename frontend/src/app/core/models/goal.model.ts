/** Mirrors GOAL in docs/architecture/architecture.md §2 (PA-01). */
export interface Goal {
  goalId: string;
  description: string;
  type: string;
  /** architecture.md §7.3's lifecycle: Intake/Decomposing/PlanProposed/Approved/... */
  status: string;
  mediaIds?: string[];
  /** Set when this goal is about one specific plant, not the whole garden. */
  plantId?: string;
}

/** Client API request for `POST /gardens/{gardenId}/goals` (WS-03/WS-05). */
export interface CreateGoalRequest {
  description: string;
  /** Ids from prior requestMediaUpload calls, once each upload has completed. */
  mediaIds?: string[];
  /** Propagates to every attached Media record and every Task the orchestrator proposes. */
  plantId?: string;
}
