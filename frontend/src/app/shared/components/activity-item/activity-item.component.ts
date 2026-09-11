import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { IconComponent } from '../icon/icon.component';
import { ActivityEvent } from '../../../core/models/activity.model';

/** One entry in the Activity screen's timeline. */
@Component({
  selector: 'td-activity-item',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './activity-item.component.html',
  styleUrl: './activity-item.component.scss',
})
export class ActivityItemComponent {
  @Input({ required: true }) event!: ActivityEvent;
  @Input() last = false;
}
