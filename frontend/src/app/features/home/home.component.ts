import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toObservable, toSignal } from '@angular/core/rxjs-interop';
import { Router, RouterLink } from '@angular/router';
import { switchMap } from 'rxjs/operators';
import { TaskRowComponent } from '../../shared/components/task-row/task-row.component';
import { GoalCardComponent } from '../../shared/components/goal-card/goal-card.component';
import { PlantCardComponent } from '../../shared/components/plant-card/plant-card.component';
import { TaskApi } from '../../core/services/task.service';
import { GoalApi } from '../../core/services/goal.service';
import { GardenApi } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';

// A goal further along than this is "in progress"; anything earlier (Intake/Decomposing/
// PlanProposed — architecture.md §7.3) still needs the gardener's attention (a question to
// answer, or a plan to approve) before Tendril can act on it.
const IN_PROGRESS_STATUSES = new Set(['Approved', 'InProgress']);

@Component({
  selector: 'td-home',
  standalone: true,
  imports: [RouterLink, TaskRowComponent, GoalCardComponent, PlantCardComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './home.component.html',
  styleUrl: './home.component.scss',
})
export class HomeComponent {
  private readonly taskApi = inject(TaskApi);
  private readonly goalApi = inject(GoalApi);
  private readonly gardenApi = inject(GardenApi);
  private readonly currentGarden = inject(CurrentGardenService);
  private readonly router = inject(Router);

  readonly todayTasks = this.taskApi.todayTasks;

  private readonly allGoals = toSignal(
    toObservable(this.currentGarden.gardenId).pipe(
      switchMap((gardenId) => this.goalApi.getGoals(gardenId)),
    ),
    { initialValue: [] },
  );
  readonly goalsInProgress = computed(() =>
    this.allGoals().filter((g) => IN_PROGRESS_STATUSES.has(g.status)),
  );
  // Real replacement for the fully-fictitious "Waiting on you" banner removed earlier — backed
  // by actual Goal status, not a static string.
  readonly goalsNeedingAttention = computed(() =>
    this.allGoals().filter((g) => !IN_PROGRESS_STATUSES.has(g.status)),
  );

  readonly plants = toSignal(
    toObservable(this.currentGarden.gardenId).pipe(
      switchMap((gardenId) => this.gardenApi.getPlantsSummary(gardenId)),
    ),
    { initialValue: [] },
  );

  toggleTask(taskId: string): void {
    this.taskApi.toggle(taskId);
  }

  goToGarden(): void {
    this.router.navigateByUrl('/garden');
  }
}
