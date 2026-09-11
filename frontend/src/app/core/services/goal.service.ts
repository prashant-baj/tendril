import { Injectable } from '@angular/core';
import { Observable, of } from 'rxjs';
import { Goal, GoalFact, GoalStat } from '../models/goal.model';
import { Finding, FollowUp, PlanTask } from '../models/plan-task.model';
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
 * Only `g1` (the tomato goal) has full detail data — this mirrors the mockup, where every
 * goal card's `onClick` always opens the same mocked goal-detail screen regardless of which
 * goal was clicked. Wiring per-goal detail is real backend work, not a design-porting concern.
 */
export abstract class GoalApi {
  abstract getGoals(): Observable<Goal[]>;
  abstract getGoalDetail(goalId: string): Observable<GoalDetail | undefined>;
  abstract getGoalFacts(): Observable<GoalFact[]>;
  abstract getFindings(): Observable<Finding[]>;
  /** Human-in-the-loop plan approval (ADR-0004 `POST /plans/{id}/approve`). Mocked: no-op. */
  abstract approve(goalId: string): Observable<void>;
}

@Injectable({ providedIn: 'root' })
export class MockGoalApi extends GoalApi {
  static readonly PRIMARY_GOAL_ID = 'g1';

  private readonly goals: Goal[] = [
    {
      goalId: 'g1',
      title: 'Get Tomato #2 setting fruit within 3 weeks',
      garden: 'Balcony Kitchen Garden',
      status: 'in-progress',
      statusLabel: 'In progress',
      statusIcon: 'pending',
      timing: 'Day 5 of 21',
      next: 'Next check-in tomorrow',
      progressPct: 62,
      successCriteria: 'at least 5 fruits have set and no new lower-leaf yellowing appears for 7 days',
    },
    {
      goalId: 'g2',
      title: 'Maximum rose blooms by the festival',
      garden: 'Front Rose Bed',
      status: 'in-progress',
      statusLabel: 'In progress',
      statusIcon: 'pending',
      timing: 'Week 2 of 6',
      next: 'Prune done · feeding Friday',
      progressPct: 28,
      successCriteria: '',
    },
    {
      goalId: 'g3',
      title: 'Clear black spot on the rose bed',
      garden: 'Front Rose Bed',
      status: 'completed',
      statusLabel: 'Completed',
      statusIcon: 'check_circle',
      timing: 'Closed 2 Sep',
      next: 'No recurrence for 14 days',
      progressPct: 100,
      successCriteria: '',
    },
  ];

  private readonly goalFacts: GoalFact[] = [
    { icon: 'schedule', label: '3 weeks' },
    { icon: 'task_alt', label: '4 tasks' },
    { icon: 'groups', label: '6 specialists' },
    { icon: 'event_repeat', label: '5 check-ins' },
  ];

  private readonly findings: Finding[] = [
    { title: 'Nitrogen imbalance for the fruiting stage', detail: 'Lower-leaf yellowing with healthy flowers is the classic pattern. Agronomy recommends a high-potassium feed.', icon: 'science', confidence: '86%' },
    { title: 'Heat stress is blocking fruit set', detail: 'Pollen goes sterile above about 33 °C. The forecast holds 34 °C through Thursday.', icon: 'thermostat', confidence: '74%' },
  ];

  private readonly trace: SpecialistTraceEntry[] = [
    { agent: 'Vision & diagnosis', icon: 'photo_camera', says: 'Lower-leaf chlorosis; flowers intact, no pest damage.', ms: '1.4s' },
    { agent: 'Agronomy', icon: 'science', says: 'Nitrogen is high for a fruiting plant — move to a high-potassium feed.', ms: '2.1s' },
    { agent: 'Weather', icon: 'thermostat', says: '34 °C spell through Thursday, then dry Friday to Sunday.', ms: '0.6s' },
    { agent: 'Pollination', icon: 'hive', says: 'A 3rd-floor balcony gets almost no pollinators — hand-pollinate daily.', ms: '1.8s' },
    { agent: 'Irrigation', icon: 'water_drop', says: 'Deep-water alternate mornings; mulch the pots to hold moisture.', ms: '1.2s' },
    { agent: 'Orchestrator', icon: 'graph_3', says: 'Feed change and hand-pollination start now; neem spray waits for Saturday’s dry window.', ms: '3.9s', isOrchestrator: true },
  ];

  private readonly planTasks: PlanTask[] = [
    { whenTop: 'Today', whenBig: '1', title: 'Hand-pollinate every morning', detail: 'Tap each open flower cluster gently around 7 AM, before the heat builds.', scope: 'Tomato #2', scopeIcon: 'potted_plant', source: 'Pollination', gate: 'Daily, 14 days', gateIcon: 'event_repeat', gateTone: 'neutral' },
    { whenTop: 'Wed', whenBig: '3', title: 'Switch to a bloom & fruit feed', detail: 'Half dose of a high-potassium feed. Stop the current all-purpose fertiliser.', scope: 'Tomato #2, Chilli #1', scopeIcon: 'science', source: 'Agronomy', gate: 'One-off', gateIcon: 'check_circle', gateTone: 'neutral' },
    { whenTop: 'Thu', whenBig: '4', title: 'Mulch the pots and deep-water alternate mornings', detail: 'Dry grass or coco peat, 2 cm. Cuts soil temperature and evaporation during the spell.', scope: 'Tomato #2', scopeIcon: 'water_drop', source: 'Irrigation + Weather', gate: 'Heat spell', gateIcon: 'thermostat', gateTone: 'weather' },
    { whenTop: 'Sat', whenBig: '6', title: 'Preventive neem spray, after sunset', detail: 'Only if the forecast stays dry. Tendril will confirm on Friday evening before reminding you.', scope: 'Whole garden', scopeIcon: 'yard', source: 'Pest', gate: 'Weather-gated', gateIcon: 'rainy', gateTone: 'pest' },
  ];

  private readonly followUps: FollowUp[] = [
    { icon: 'photo_camera', what: 'Photo of the lower leaves', when: 'Every 3 days' },
    { icon: 'chat', what: '“Any fruit set yet?” on WhatsApp', when: 'Day 7' },
    { icon: 'thermostat', what: 'Weather re-check before the spray', when: 'Fri evening' },
    { icon: 'flag', what: 'Goal re-assessed against success criteria', when: 'Day 14 and 21' },
  ];

  private readonly stats: GoalStat[] = [
    { value: '18', label: 'days' },
    { value: '4', label: 'tasks' },
    { value: '5', label: 'check-ins' },
  ];

  getGoals(): Observable<Goal[]> {
    return of(this.goals);
  }

  getGoalDetail(goalId: string): Observable<GoalDetail | undefined> {
    const goal = this.goals.find((g) => g.goalId === goalId);
    if (!goal || goalId !== MockGoalApi.PRIMARY_GOAL_ID) {
      return of(undefined);
    }
    return of({ goal, stats: this.stats, trace: this.trace, planTasks: this.planTasks, followUps: this.followUps });
  }

  getGoalFacts(): Observable<GoalFact[]> {
    return of(this.goalFacts);
  }

  getFindings(): Observable<Finding[]> {
    return of(this.findings);
  }

  approve(_goalId: string): Observable<void> {
    // Mocked — a real implementation calls `POST /plans/{id}/approve` (ADR-0004).
    return of(undefined);
  }
}
