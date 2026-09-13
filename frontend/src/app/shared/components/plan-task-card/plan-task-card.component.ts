import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { ChipComponent } from '../chip/chip.component';
import { Task } from '../../../core/models/plan.model';

/** One task in a goal's proposed/approved plan, shown on the Goal-detail screen's plan list. */
@Component({
  selector: 'td-plan-task-card',
  standalone: true,
  imports: [ChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './plan-task-card.component.html',
  styleUrl: './plan-task-card.component.scss',
})
export class PlanTaskCardComponent {
  @Input({ required: true }) task!: Task;
}
