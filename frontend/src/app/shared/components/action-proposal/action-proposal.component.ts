import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { GoalAction } from '../../../core/models/message.model';
import { IconComponent } from '../icon/icon.component';

/**
 * A change Tendril proposed in the chat (ADR-0034): its question, and Confirm / Not now while it's
 * open. Confirm is the only way the change is applied — a typed "yes" never is. Purely
 * presentational; the goal screen calls the confirm/reject endpoints.
 */
@Component({
  selector: 'td-action-proposal',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './action-proposal.component.html',
  styleUrl: './action-proposal.component.scss',
})
export class ActionProposalComponent {
  @Input({ required: true }) action!: GoalAction;
  @Input() busy = false;
  @Output() confirm = new EventEmitter<GoalAction>();
  @Output() reject = new EventEmitter<GoalAction>();

  /** The second candidate, offered as an example of correcting an unsure read in words. */
  get alternative(): number | undefined {
    const c = this.action.candidates ?? [];
    return c.length > 1 ? c[1] : undefined;
  }
}
