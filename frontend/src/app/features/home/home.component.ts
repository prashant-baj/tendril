import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { TaskRowComponent } from '../../shared/components/task-row/task-row.component';
import { GoalCardComponent } from '../../shared/components/goal-card/goal-card.component';
import { PlantCardComponent } from '../../shared/components/plant-card/plant-card.component';
import { TaskApi } from '../../core/services/task.service';
import { GoalApi, MockGoalApi } from '../../core/services/goal.service';
import { GardenApi } from '../../core/services/garden.service';

@Component({
  selector: 'td-home',
  standalone: true,
  imports: [RouterLink, IconComponent, TaskRowComponent, GoalCardComponent, PlantCardComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './home.component.html',
  styleUrl: './home.component.scss',
})
export class HomeComponent {
  private readonly taskApi = inject(TaskApi);
  private readonly goalApi = inject(GoalApi);
  private readonly gardenApi = inject(GardenApi);
  private readonly router = inject(Router);

  readonly todayTasks = this.taskApi.todayTasks;
  readonly goals = toSignal(this.goalApi.getGoals(), { initialValue: [] });
  readonly plants = toSignal(this.gardenApi.getPlantsSummary(), { initialValue: [] });

  readonly reviewPlanRoute = ['/goals', MockGoalApi.PRIMARY_GOAL_ID];

  toggleTask(taskId: string): void {
    this.taskApi.toggle(taskId);
  }

  goToGarden(): void {
    this.router.navigateByUrl('/garden');
  }
}
