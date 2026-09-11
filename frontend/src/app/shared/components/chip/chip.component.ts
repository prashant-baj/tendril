import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { IconComponent } from '../icon/icon.component';

export type ChipTone = 'neutral' | 'amber' | 'green' | 'red';
export type ChipShape = 'pill' | 'rect';
export type ChipVariant = 'solid' | 'outline';

/**
 * A small labelled badge — replaces the mockup's ad hoc `chip()` / `statusChip()`
 * inline-style-string helpers with real inputs + SCSS tone classes. Used for task chips,
 * goal status, plan-task gates, and garden/goal fact pills.
 */
@Component({
  selector: 'td-chip',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chip.component.html',
  styleUrl: './chip.component.scss',
})
export class ChipComponent {
  @Input() tone: ChipTone = 'neutral';
  @Input() shape: ChipShape = 'rect';
  @Input() variant: ChipVariant = 'solid';
  @Input() icon?: string;
  @Input() iconFilled = false;
}
