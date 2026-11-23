import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, of } from 'rxjs';
import { map } from 'rxjs/operators';
import { CLIENT_API_BASE_URL } from '../config/client-api.config';
import { Goal } from '../models/goal.model';
import { Plan, Task } from '../models/plan.model';
import { ChatMessage, GoalAction } from '../models/message.model';

/** A goal's attached photo(s), with a freshly-generated presigned download URL (PA-03). */
export interface GoalMedia {
  mediaId: string;
  downloadUrl: string;
}

/** Full detail for one goal: its proposed/approved plan (if any), tasks, photos, and chat
 * thread (PA-01/PA-02/PA-03). */
export interface GoalDetail {
  goal: Goal;
  plan?: Plan;
  tasks: Task[];
  media: GoalMedia[];
  messages: ChatMessage[];
  /** Proposals on this goal, oldest first (ADR-0034). */
  actions?: GoalAction[];
}

/** Extras a chat message can carry (ADR-0034): photos, and a task to check in on. */
export interface MessageExtras {
  mediaIds?: string[];
  taskId?: string;
}

/**
 * Goal + Plan read/write model (GOAL/PLAN/TASK/MESSAGE entities, architecture.md §2). Shaped
 * after `GET/POST /gardens/{id}/goals[/{goalId}]`, `POST /gardens/{id}/goals/{goalId}/messages`,
 * and `POST /gardens/{id}/plans/{planId}/approve` (PA-01/PA-02) so a real `HttpGoalApi` can
 * replace `MockGoalApi` later.
 */
export abstract class GoalApi {
  abstract getGoals(gardenId: string | null): Observable<Goal[]>;
  abstract getGoalDetail(gardenId: string | null, goalId: string): Observable<GoalDetail | undefined>;
  /** PA-02: send a chat message about a goal — asks/adjusts the proposed plan. Async (202). */
  abstract sendMessage(
    gardenId: string,
    goalId: string,
    content: string,
    extras?: MessageExtras,
  ): Observable<void>;
  /** ADR-0034: apply a proposed action (the only way it changes anything). */
  abstract confirmAction(gardenId: string, goalId: string, actionId: string): Observable<void>;
  /** ADR-0034: dismiss a proposed action. */
  abstract rejectAction(gardenId: string, goalId: string, actionId: string): Observable<void>;
  /** PA-02: approve a proposed plan — a synchronous, deterministic status flip. */
  abstract approve(gardenId: string, planId: string): Observable<void>;
  /** Checks in on a task with a photo. ADR-0034: proposes completion; Confirm marks it done. */
  abstract checkinTask(
    gardenId: string,
    goalId: string,
    taskId: string,
    mediaId: string,
  ): Observable<void>;
}

@Injectable({ providedIn: 'root' })
export class MockGoalApi extends GoalApi {
  private readonly goals: Goal[] = [];

  getGoals(_gardenId: string | null): Observable<Goal[]> {
    return of(this.goals);
  }

  getGoalDetail(_gardenId: string | null, _goalId: string): Observable<GoalDetail | undefined> {
    return of(undefined);
  }

  sendMessage(
    _gardenId: string,
    _goalId: string,
    _content: string,
    _extras?: MessageExtras,
  ): Observable<void> {
    return of(undefined);
  }

  confirmAction(_gardenId: string, _goalId: string, _actionId: string): Observable<void> {
    return of(undefined);
  }

  rejectAction(_gardenId: string, _goalId: string, _actionId: string): Observable<void> {
    return of(undefined);
  }

  approve(_gardenId: string, _planId: string): Observable<void> {
    return of(undefined);
  }

  checkinTask(
    _gardenId: string,
    _goalId: string,
    _taskId: string,
    _mediaId: string,
  ): Observable<void> {
    return of(undefined);
  }
}

interface GoalDto {
  goalId: string;
  description: string;
  type: string;
  status: string;
  mediaIds?: string[];
  plantId?: string;
}

interface GoalDetailDto {
  goal: GoalDto;
  plan?: Plan;
  tasks: Task[];
  media: GoalMedia[];
  messages: ChatMessage[];
  actions?: GoalAction[];
}

/**
 * Real Client API implementation of the goal read/chat/approve operations (PA-01/PA-02/PA-03).
 * No goal-list/detail backend existed before this — `MockGoalApi` is now only the
 * pre-onboarding fallback (no garden created yet), same posture as `HttpGardenApi`.
 */
@Injectable({ providedIn: 'root' })
export class HttpGoalApi extends GoalApi {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(CLIENT_API_BASE_URL);
  private readonly mock = new MockGoalApi();

  getGoals(gardenId: string | null): Observable<Goal[]> {
    if (!gardenId) {
      return this.mock.getGoals(gardenId);
    }
    return this.http.get<GoalDto[]>(`${this.baseUrl}/gardens/${gardenId}/goals`);
  }

  getGoalDetail(gardenId: string | null, goalId: string): Observable<GoalDetail | undefined> {
    if (!gardenId) {
      return this.mock.getGoalDetail(gardenId, goalId);
    }
    return this.http
      .get<GoalDetailDto>(`${this.baseUrl}/gardens/${gardenId}/goals/${goalId}`)
      .pipe(map((dto) => dto as GoalDetail));
  }

  sendMessage(
    gardenId: string,
    goalId: string,
    content: string,
    extras?: MessageExtras,
  ): Observable<void> {
    const body: Record<string, unknown> = { content };
    if (extras?.mediaIds?.length) body['mediaIds'] = extras.mediaIds;
    if (extras?.taskId) body['taskId'] = extras.taskId;
    return this.http
      .post<void>(`${this.baseUrl}/gardens/${gardenId}/goals/${goalId}/messages`, body)
      .pipe(map(() => undefined));
  }

  confirmAction(gardenId: string, goalId: string, actionId: string): Observable<void> {
    return this.http
      .post<void>(
        `${this.baseUrl}/gardens/${gardenId}/goals/${goalId}/actions/${actionId}/confirm`,
        {},
      )
      .pipe(map(() => undefined));
  }

  rejectAction(gardenId: string, goalId: string, actionId: string): Observable<void> {
    return this.http
      .post<void>(
        `${this.baseUrl}/gardens/${gardenId}/goals/${goalId}/actions/${actionId}/reject`,
        {},
      )
      .pipe(map(() => undefined));
  }

  approve(gardenId: string, planId: string): Observable<void> {
    return this.http
      .post<void>(`${this.baseUrl}/gardens/${gardenId}/plans/${planId}/approve`, {})
      .pipe(map(() => undefined));
  }

  checkinTask(
    gardenId: string,
    goalId: string,
    taskId: string,
    mediaId: string,
  ): Observable<void> {
    return this.http
      .post<void>(
        `${this.baseUrl}/gardens/${gardenId}/goals/${goalId}/tasks/${taskId}/checkins`,
        { mediaId },
      )
      .pipe(map(() => undefined));
  }
}
