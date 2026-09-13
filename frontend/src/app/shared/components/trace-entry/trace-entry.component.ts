import { ChangeDetectionStrategy, Component, Input, signal } from '@angular/core';
import { IconComponent } from '../icon/icon.component';
import { MarkdownPipe } from '../../pipes/markdown.pipe';
import { SpecialistTraceEntry } from '../../../core/models/plan.model';
import { specialistIcon, specialistLabel } from '../specialist-icon.util';

// Above this length, the entry starts visually clamped (CSS max-height) behind a "Show more"
// toggle — `says` is now the specialist's full markdown response (the backend stopped
// display-truncating it), so long entries need a frontend-side collapse. The full markdown is
// always rendered (never string-sliced) so an expand never reveals a broken/half-closed
// `**bold**` or `### heading` from being cut mid-token.
const COLLAPSE_LENGTH = 220;

/** One specialist's (or the orchestrator's own) node in Goal Detail's "How this was decided"
 * timeline (PA-05) — real data now, recreated from the original fixture-only mockup
 * (`trace-entry.component.*` in commit 7f3b810, deleted in 1c8a8a9). */
@Component({
  selector: 'td-trace-entry',
  standalone: true,
  imports: [IconComponent, MarkdownPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './trace-entry.component.html',
  styleUrl: './trace-entry.component.scss',
})
export class TraceEntryComponent {
  @Input({ required: true }) entry!: SpecialistTraceEntry;
  @Input() last = false;

  readonly expanded = signal(false);

  get icon(): string {
    return specialistIcon(this.entry.agent);
  }

  get label(): string {
    return specialistLabel(this.entry.agent);
  }

  get durationLabel(): string {
    return `${(this.entry.ms / 1000).toFixed(1)}s`;
  }

  get isTruncatable(): boolean {
    return this.entry.says.length > COLLAPSE_LENGTH;
  }

  toggle(): void {
    this.expanded.update((v) => !v);
  }
}
