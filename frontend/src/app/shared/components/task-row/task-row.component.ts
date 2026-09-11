import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { IconComponent } from '../icon/icon.component';
import { ChipComponent, ChipTone } from '../chip/chip.component';
import { TaskItem } from '../../../core/models/task.model';

const TONE_MAP: Record<TaskItem['tone'], ChipTone> = {
  due: 'amber',
  soon: 'neutral',
  gated: 'red',
};

/** One row in a task list — toggle-to-complete, used by both Home and the Tasks screen. */
@Component({
  selector: 'td-task-row',
  standalone: true,
  imports: [IconComponent, ChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './task-row.component.html',
  styleUrl: './task-row.component.scss',
})
export class TaskRowComponent {
  @Input({ required: true }) task!: TaskItem;
  @Output() toggle = new EventEmitter<string>();

  get chipTone(): ChipTone {
    return this.task.done ? 'green' : TONE_MAP[this.task.tone];
  }
}
