/** One turn in a Goal's chat thread (PA-02). */
export interface ChatMessage {
  messageId?: string;
  role: 'user' | 'assistant';
  /** May be empty for a user message that only carries a photo or a task (ADR-0034). */
  content: string;
  createdAt: string;
  /** Photos on a user message (thumbnail URLs). */
  media?: { mediaId: string; downloadUrl: string }[];
  /** Set when the message is a check-in on this task. */
  taskId?: string;
  /** On an assistant reply: the proposed action it asks the gardener to confirm. */
  actionId?: string;
}

/** A change the orchestrator proposed (ADR-0034). Only Confirm applies it. */
export interface GoalAction {
  actionId: string;
  type: 'complete_task' | 'set_plant' | 'set_goal_plant' | 'create_plant';
  summary: string;
  status: 'proposed' | 'confirmed' | 'rejected' | 'superseded';
  createdAt: string;
  taskId?: string;
  /** Stake numbers to choose from when the read was unsure. */
  candidates?: number[];
}
