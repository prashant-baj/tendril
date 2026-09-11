import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { map, switchMap } from 'rxjs';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { ChipComponent } from '../../shared/components/chip/chip.component';
import { TraceEntryComponent } from '../../shared/components/trace-entry/trace-entry.component';
import { PlanTaskCardComponent } from '../../shared/components/plan-task-card/plan-task-card.component';
import { FollowUpRowComponent } from '../../shared/components/follow-up-row/follow-up-row.component';
import { GoalApi } from '../../core/services/goal.service';

@Component({
  selector: 'td-goal-detail',
  standalone: true,
  imports: [RouterLink, IconComponent, ChipComponent, TraceEntryComponent, PlanTaskCardComponent, FollowUpRowComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './goal-detail.component.html',
  styleUrl: './goal-detail.component.scss',
})
export class GoalDetailComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly goalApi = inject(GoalApi);

  readonly detail = toSignal(
    this.route.paramMap.pipe(
      map((params) => params.get('goalId') ?? ''),
      switchMap((goalId) => this.goalApi.getGoalDetail(goalId)),
    ),
    { initialValue: undefined },
  );

  readonly traceOpen = signal(false);

  toggleTrace(): void {
    this.traceOpen.update((open) => !open);
  }
}
