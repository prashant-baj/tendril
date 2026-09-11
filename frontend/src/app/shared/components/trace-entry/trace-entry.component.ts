import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { IconComponent } from '../icon/icon.component';
import { SpecialistTraceEntry } from '../../../core/models/trace.model';

/** One specialist's node in the goal-detail "How this was decided" timeline. */
@Component({
  selector: 'td-trace-entry',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './trace-entry.component.html',
  styleUrl: './trace-entry.component.scss',
})
export class TraceEntryComponent {
  @Input({ required: true }) entry!: SpecialistTraceEntry;
  @Input() last = false;
}
