import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { IconComponent } from '../icon/icon.component';
import { Finding } from '../../../core/models/plan-task.model';

/** One diagnosis finding on the capture "found" screen. */
@Component({
  selector: 'td-finding-card',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './finding-card.component.html',
  styleUrl: './finding-card.component.scss',
})
export class FindingCardComponent {
  @Input({ required: true }) finding!: Finding;
}
