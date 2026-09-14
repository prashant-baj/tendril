import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { IconComponent } from '../icon/icon.component';
import { ActivityEvent } from '../../../core/models/activity.model';
import { activityPresentation } from '../activity-presentation.util';

/** One entry in the Activity screen's timeline (Phase 6 — real events, not fixture data). */
@Component({
  selector: 'td-activity-item',
  standalone: true,
  imports: [RouterLink, IconComponent, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './activity-item.component.html',
  styleUrl: './activity-item.component.scss',
})
export class ActivityItemComponent {
  @Input({ required: true }) event!: ActivityEvent;
  @Input() last = false;

  get presentation() {
    return activityPresentation(this.event);
  }
}
