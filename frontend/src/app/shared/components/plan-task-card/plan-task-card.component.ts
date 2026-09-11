import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { ChipComponent, ChipTone } from '../chip/chip.component';
import { GateTone, PlanTask } from '../../../core/models/plan-task.model';

const GATE_TONE_MAP: Record<GateTone, ChipTone> = {
  neutral: 'neutral',
  weather: 'amber',
  pest: 'red',
};

/** One task in a goal's approved plan, shown on the Goal-detail screen's plan list. */
@Component({
  selector: 'td-plan-task-card',
  standalone: true,
  imports: [ChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './plan-task-card.component.html',
  styleUrl: './plan-task-card.component.scss',
})
export class PlanTaskCardComponent {
  @Input({ required: true }) task!: PlanTask;

  get gateTone(): ChipTone {
    return GATE_TONE_MAP[this.task.gateTone];
  }
}
