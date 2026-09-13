import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { toObservable, toSignal } from '@angular/core/rxjs-interop';
import { switchMap } from 'rxjs/operators';
import { ActivityItemComponent } from '../../shared/components/activity-item/activity-item.component';
import { ActivityApi } from '../../core/services/activity.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';

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
  private readonly currentGarden = inject(CurrentGardenService);

  readonly activity = toSignal(
    toObservable(this.currentGarden.gardenId).pipe(
      switchMap((gardenId) => this.activityApi.getActivity(gardenId)),
    ),
    { initialValue: [] },
  );
}
