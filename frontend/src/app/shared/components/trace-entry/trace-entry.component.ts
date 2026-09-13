import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { IconComponent } from '../icon/icon.component';
import { SpecialistTraceEntry } from '../../../core/models/plan.model';
import { specialistIcon, specialistLabel } from '../specialist-icon.util';

/** One specialist's (or the orchestrator's own) node in Goal Detail's "How this was decided"
 * timeline (PA-05) — real data now, recreated from the original fixture-only mockup
 * (`trace-entry.component.*` in commit 7f3b810, deleted in 1c8a8a9). */
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

  get icon(): string {
    return specialistIcon(this.entry.agent);
  }

  get label(): string {
    return specialistLabel(this.entry.agent);
  }

  get durationLabel(): string {
    return `${(this.entry.ms / 1000).toFixed(1)}s`;
  }
}
