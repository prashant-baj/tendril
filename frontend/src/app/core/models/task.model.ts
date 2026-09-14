import { Task } from './plan.model';

/**
 * One task in a garden's cross-goal task list (Phase 6, `GET /gardens/{id}/tasks`) — the same
 * `Task` shape Goal Detail already uses, plus `goalId` so a flat, cross-goal list can link each
 * row back to the goal it belongs to (Goal Detail already knows its own goalId from the route,
 * so `Task` itself doesn't carry it there).
 */
export interface GardenTaskItem extends Task {
  goalId: string;
}

export interface TaskGroup {
  /** "To do" or "Done" — real tasks have no due-date/schedule concept yet (that's Phase 7+
   * tracker work), so grouping is by status, not by the original mockup's Today/This week/Later. */
  title: string;
  items: GardenTaskItem[];
}
