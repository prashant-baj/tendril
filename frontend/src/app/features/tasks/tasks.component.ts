import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toObservable, toSignal } from '@angular/core/rxjs-interop';
import { switchMap } from 'rxjs/operators';
import { TaskRowComponent } from '../../shared/components/task-row/task-row.component';
import { TaskApi } from '../../core/services/task.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import { TaskGroup } from '../../core/models/task.model';

/**
 * Every task across every goal in the garden, grouped by status (Phase 6) — real tasks have no
 * due-date/schedule concept yet (that's Phase 7+ tracker work), so "To do"/"Done" replaces the
 * original mockup's Today/This week/Later.
 */
@Component({
  selector: 'td-tasks',
  standalone: true,
  imports: [TaskRowComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './tasks.component.html',
  styleUrl: './tasks.component.scss',
})
export class TasksComponent {
  private readonly taskApi = inject(TaskApi);
  private readonly currentGarden = inject(CurrentGardenService);

  private readonly allTasks = toSignal(
    toObservable(this.currentGarden.gardenId).pipe(
      switchMap((gardenId) => this.taskApi.getTasks(gardenId)),
    ),
    { initialValue: [] },
  );

  readonly taskGroups = computed<TaskGroup[]>(() => [
    { title: 'To do', items: this.allTasks().filter((t) => t.status !== 'done') },
    { title: 'Done', items: this.allTasks().filter((t) => t.status === 'done') },
  ]);
}
