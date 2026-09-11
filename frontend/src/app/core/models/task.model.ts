/** Presentation tone for a task row's trailing chip and check icon. */
export type TaskTone = 'due' | 'soon' | 'gated';

/** Mirrors the TASK entity in docs/architecture/architecture.md §2, plus UI presentation. */
export interface TaskItem {
  taskId: string;
  label: string;
  meta: string;
  metaIcon: string;
  chip: string;
  tone: TaskTone;
  done: boolean;
}

export interface TaskGroup {
  title: string;
  items: TaskItem[];
}
