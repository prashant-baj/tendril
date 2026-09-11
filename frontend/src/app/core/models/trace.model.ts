/** One specialist's contribution in the orchestrator's decision trace. */
export interface SpecialistTraceEntry {
  agent: string;
  icon: string;
  says: string;
  ms: string;
  isOrchestrator?: boolean;
}

export type LiveStepState = 'done' | 'active' | 'todo';

/** A specialist step as it's revealed during the capture "analyzing" phase. */
export interface LiveStep {
  name: string;
  icon: string;
  note: string;
  state: LiveStepState;
}
