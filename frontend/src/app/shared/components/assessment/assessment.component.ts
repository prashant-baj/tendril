import { ChangeDetectionStrategy, Component, Input, signal } from '@angular/core';
import { IconComponent } from '../icon/icon.component';
import { Assessment, Verdict } from '../../../core/models/farm-work.model';

const LABEL: Record<Verdict, string> = {
  beneficial: 'Beneficial',
  caution: 'Caution',
  harmful: 'Harmful',
  unknown: "Couldn't assess",
};
const ICON: Record<Verdict, string> = {
  beneficial: 'check_circle',
  caution: 'warning',
  harmful: 'dangerous',
  unknown: 'help',
};

/**
 * The Supervisor's verdict on a piece of farm work (ADR-0038 §4): a coloured badge that expands to
 * the summary, benefits, risks, conflicts and suggestion. A harmful verdict opens by default as a
 * red banner. Advisory only — nothing here blocks the owner. No assessment yet shows "Checking…".
 */
@Component({
  selector: 'td-assessment',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './assessment.component.html',
  styleUrl: './assessment.component.scss',
})
export class AssessmentComponent {
  @Input() assessment: Assessment | undefined;

  private readonly toggled = signal<boolean | null>(null);

  get verdict(): Verdict | 'pending' {
    return this.assessment?.verdict ?? 'pending';
  }

  get label(): string {
    return this.assessment ? LABEL[this.assessment.verdict] : 'Checking…';
  }

  get icon(): string {
    return this.assessment ? ICON[this.assessment.verdict] : 'hourglass_empty';
  }

  get open(): boolean {
    return this.toggled() ?? this.assessment?.verdict === 'harmful';
  }

  toggle(): void {
    if (this.assessment) {
      this.toggled.set(!this.open);
    }
  }
}
