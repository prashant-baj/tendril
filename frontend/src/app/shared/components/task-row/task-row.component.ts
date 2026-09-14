import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { IconComponent } from '../icon/icon.component';
import { ChipComponent, ChipTone } from '../chip/chip.component';
import { GardenTaskItem } from '../../../core/models/task.model';

/** One row in the garden's cross-goal task list (Phase 6) — used by both Home's "Today" widget
 * and the Tasks screen. Navigates to the task's own goal (real check-in/chat live there, not
 * duplicated here) instead of the old mockup's plain toggle-to-complete. */
@Component({
  selector: 'td-task-row',
  standalone: true,
  imports: [RouterLink, IconComponent, ChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './task-row.component.html',
  styleUrl: './task-row.component.scss',
})
export class TaskRowComponent {
  @Input({ required: true }) task!: GardenTaskItem;

  get detailRoute(): string[] {
    return ['/goals', this.task.goalId];
  }

  get done(): boolean {
    return this.task.status === 'done';
  }

  get chipTone(): ChipTone {
    return this.done ? 'green' : 'neutral';
  }
}
