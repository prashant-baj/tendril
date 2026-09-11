import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { TaskRowComponent } from '../../shared/components/task-row/task-row.component';
import { TaskApi } from '../../core/services/task.service';

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
  readonly taskGroups = this.taskApi.taskGroups;

  toggleTask(taskId: string): void {
    this.taskApi.toggle(taskId);
  }
}
