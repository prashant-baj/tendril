import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ChipComponent, ChipTone } from '../chip/chip.component';
import { Goal } from '../../../core/models/goal.model';
import { goalStatusLabel, goalStatusTone } from '../goal-status.util';

/** A goal-in-progress card on the Home screen's "Goals in progress" grid. */
@Component({
  selector: 'td-goal-card',
  standalone: true,
  imports: [RouterLink, ChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './goal-card.component.html',
  styleUrl: './goal-card.component.scss',
})
export class GoalCardComponent {
  @Input({ required: true }) goal!: Goal;

  get detailRoute(): string[] {
    return ['/goals', this.goal.goalId];
  }

  get statusTone(): ChipTone {
    return goalStatusTone(this.goal.status);
  }

  get statusLabel(): string {
    return goalStatusLabel(this.goal.status);
  }
}
