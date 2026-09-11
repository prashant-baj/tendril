import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivityItemComponent } from '../../shared/components/activity-item/activity-item.component';
import { ActivityApi } from '../../core/services/activity.service';

@Component({
  selector: 'td-activity',
  standalone: true,
  imports: [ActivityItemComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './activity.component.html',
  styleUrl: './activity.component.scss',
})
export class ActivityComponent {
  private readonly activityApi = inject(ActivityApi);
  readonly activity = toSignal(this.activityApi.getActivity(), { initialValue: [] });
}
