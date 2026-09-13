import { Injectable } from '@angular/core';
import { Observable, of } from 'rxjs';
import { Goal, GoalStat } from '../models/goal.model';
import { FollowUp, PlanTask } from '../models/plan-task.model';
import { SpecialistTraceEntry } from '../models/trace.model';

/** Full detail for a goal whose plan has been proposed/approved. */
export interface GoalDetail {
  goal: Goal;
  stats: GoalStat[];
  trace: SpecialistTraceEntry[];
  planTasks: PlanTask[];
  followUps: FollowUp[];
}

/**
 * Goal + Plan read/write model (GOAL/PLAN entities, architecture.md §2). Shaped after
 * `POST /gardens/{id}/goals` and `POST /plans/{id}/approve` (ADR-0004) so a real
 * `HttpGoalApi` can replace `MockGoalApi` later.
 *
 * No goal-list/plan-detail read endpoints exist yet (only `POST /gardens/{id}/goals`,
 * wired via `GardenApi.createGoal`) — this stays blank until that backend work is done.
 */
export abstract class GoalApi {
  abstract getGoals(): Observable<Goal[]>;
  abstract getGoalDetail(goalId: string): Observable<GoalDetail | undefined>;
  /** Human-in-the-loop plan approval (ADR-0004 `POST /plans/{id}/approve`). Mocked: no-op. */
  abstract approve(goalId: string): Observable<void>;
}

@Injectable({ providedIn: 'root' })
export class MockGoalApi extends GoalApi {
  private readonly goals: Goal[] = [];

  getGoals(): Observable<Goal[]> {
    return of(this.goals);
  }

  getGoalDetail(_goalId: string): Observable<GoalDetail | undefined> {
    return of(undefined);
  }

  approve(_goalId: string): Observable<void> {
    // Mocked — a real implementation calls `POST /plans/{id}/approve` (ADR-0004).
    return of(undefined);
  }
}
