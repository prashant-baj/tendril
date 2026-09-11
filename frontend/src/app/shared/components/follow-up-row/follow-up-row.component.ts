import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { IconComponent } from '../icon/icon.component';
import { FollowUp } from '../../../core/models/plan-task.model';

/** One scheduled check-in on the Goal-detail screen's follow-up schedule. */
@Component({
  selector: 'td-follow-up-row',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './follow-up-row.component.html',
  styleUrl: './follow-up-row.component.scss',
})
export class FollowUpRowComponent {
  @Input({ required: true }) followUp!: FollowUp;
}
