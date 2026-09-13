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

// No task read/write endpoint exists yet (ADR-0004 defines one; not implemented) — blank
// until that backend work is done.
const TODAY: TaskDef[] = [];
const THIS_WEEK: TaskDef[] = [];
const LATER: TaskDef[] = [];

const INITIAL_DONE = new Set<string>();

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
