import { Injectable, Signal, computed, signal } from '@angular/core';
import { TaskGroup, TaskItem, TaskTone } from '../models/task.model';

interface TaskDef {
  taskId: string;
  label: string;
  meta: string;
  metaIcon: string;
  chip: string;
  tone: TaskTone;
}

const TODAY: TaskDef[] = [
  { taskId: 't1', label: 'Hand-pollinate tomato flowers', meta: 'Tomato #2 · 7:00 AM', metaIcon: 'potted_plant', chip: '7:00 AM', tone: 'due' },
  { taskId: 't2', label: 'Move chilli pots to the morning-sun corner', meta: 'Whole garden', metaIcon: 'yard', chip: 'Today', tone: 'soon' },
  { taskId: 't3', label: 'Photo check-in: tomato lower leaves', meta: 'Tendril will re-read this', metaIcon: 'photo_camera', chip: '6:00 PM', tone: 'due' },
];
const THIS_WEEK: TaskDef[] = [
  { taskId: 'w1', label: 'Switch to a bloom & fruit feed, half dose', meta: 'Tomato #2, Chilli #1 · Wed', metaIcon: 'science', chip: 'Wed', tone: 'soon' },
  { taskId: 'w2', label: 'Mulch the tomato pots', meta: 'Heat spell ends Thursday · Thu', metaIcon: 'thermostat', chip: 'Thu', tone: 'soon' },
  { taskId: 'w3', label: 'Neem spray, evening only', meta: 'Waiting on a dry window · Sat', metaIcon: 'rainy', chip: 'Weather-gated', tone: 'gated' },
];
const LATER: TaskDef[] = [
  { taskId: 'l1', label: 'Re-assess fruit set against the goal', meta: 'Front Rose Bed excluded · 12 Oct', metaIcon: 'flag', chip: '12 Oct', tone: 'soon' },
];

/** t1 starts pre-completed — matches the mockup's initial `state.done` fixture exactly. */
const INITIAL_DONE = new Set(['t1']);

/**
 * Plant- and garden-level tasks (TASK entity, architecture.md §2). Shaped after
 * `POST/GET /gardens/{id}/tasks` (ADR-0004) so a real API-backed implementation can replace
 * `MockTaskApi` later.
 *
 * Task completion is tracked in one place so the Home "Today" list and the Tasks screen's
 * "Today" group (the same tasks, two views) always agree — mirrors the mockup's single
 * `state.done` map driving both.
 */
export abstract class TaskApi {
  abstract readonly todayTasks: Signal<TaskItem[]>;
  abstract readonly taskGroups: Signal<TaskGroup[]>;
  abstract toggle(taskId: string): void;
}

@Injectable({ providedIn: 'root' })
export class MockTaskApi extends TaskApi {
  private readonly done = signal<ReadonlySet<string>>(INITIAL_DONE);

  private readonly toItem = (def: TaskDef): TaskItem => {
    const isDone = this.done().has(def.taskId);
    return {
      taskId: def.taskId,
      label: def.label,
      meta: def.meta,
      metaIcon: def.metaIcon,
      chip: isDone ? 'Done' : def.chip,
      tone: def.tone,
      done: isDone,
    };
  };

  readonly todayTasks = computed(() => TODAY.map(this.toItem));
  readonly taskGroups = computed<TaskGroup[]>(() => [
    { title: 'Today', items: TODAY.map(this.toItem) },
    { title: 'This week', items: THIS_WEEK.map(this.toItem) },
    { title: 'Later', items: LATER.map(this.toItem) },
  ]);

  toggle(taskId: string): void {
    this.done.update((current) => {
      const next = new Set(current);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  }
}
