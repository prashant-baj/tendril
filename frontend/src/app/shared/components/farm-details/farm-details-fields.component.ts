import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { ReactiveFormsModule } from '@angular/forms';
import { IconComponent } from '../icon/icon.component';
import {
  CONTEXT_TEXT_MAX,
  FarmDetailsGroup,
  MAX_RECIPIENTS,
  newRecipientGroup,
} from './farm-details.util';

/**
 * The farm-details fields (ADR-0032): climate zone, soil, irrigation, intercrop, layout, notes,
 * stake range and WhatsApp recipients. Presentational only — the owning screen builds the group
 * (`buildFarmDetailsGroup`) and decides what to send. Phone numbers are stored for reminders and
 * never shown to or sent to an AI model; the hint says so.
 */
@Component({
  selector: 'td-farm-details-fields',
  standalone: true,
  imports: [ReactiveFormsModule, IconComponent],
  changeDetection: ChangeDetectionStrategy.Default,
  templateUrl: './farm-details-fields.component.html',
  styleUrl: './farm-details-fields.component.scss',
})
export class FarmDetailsFieldsComponent {
  @Input({ required: true }) group!: FarmDetailsGroup;

  readonly textMax = CONTEXT_TEXT_MAX;
  readonly maxRecipients = MAX_RECIPIENTS;

  readonly textFields = [
    { key: 'soil', label: 'Soil', placeholder: 'e.g. Heavy black clay, calcareous, some salt on the surface' },
    { key: 'irrigation', label: 'Irrigation', placeholder: 'e.g. 16 mm drip from a well, emitter 1.5 ft from the trunk' },
    { key: 'intercrop', label: 'Intercrops / ground cover', placeholder: 'e.g. Brinjal and marigold strip; chana planned between rows' },
    { key: 'layout', label: 'Layout', placeholder: 'e.g. 25×25 ft square planting, 1.5 acres' },
    { key: 'notes', label: 'Other notes', placeholder: 'Anything else your advisors should know' },
  ] as const;

  get recipients() {
    return this.group.controls.recipients;
  }

  addRecipient(): void {
    if (this.recipients.length < MAX_RECIPIENTS) {
      this.recipients.push(newRecipientGroup());
    }
  }

  removeRecipient(index: number): void {
    this.recipients.removeAt(index);
    this.group.markAsDirty();
  }
}
