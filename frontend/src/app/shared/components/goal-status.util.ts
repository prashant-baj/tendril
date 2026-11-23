import { ChipTone } from './chip/chip.component';

/** Goals Tendril is acting on (architecture.md §7.3); `InProgress` is a display-only bucket. */
export const IN_PROGRESS_STATUSES = new Set(['Approved', 'InProgress']);

/** ADR-0036: a turn ended without a plan on a goal that has none — recorded, nothing to approve. */
export const INFORMATION_SAVED = 'InformationSaved';

/** Whether a goal still needs the gardener (a plan to approve, or still being worked out). */
export function goalNeedsAttention(status: string): boolean {
  return !IN_PROGRESS_STATUSES.has(status) && status !== INFORMATION_SAVED;
}

export function goalStatusLabel(status: string): string {
  return status === INFORMATION_SAVED ? 'Saved' : status;
}

export function goalStatusTone(status: string): ChipTone {
  if (IN_PROGRESS_STATUSES.has(status)) {
    return 'green';
  }
  return status === INFORMATION_SAVED ? 'neutral' : 'amber';
}
