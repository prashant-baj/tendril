import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { ChipComponent } from '../chip/chip.component';
import { PhotoPickerComponent } from '../photo-picker/photo-picker.component';
import { Task } from '../../../core/models/plan.model';

/**
 * One task in a goal's proposed/approved plan, shown on the Goal-detail screen's plan list.
 * Purely presentational: a pending task shows a photo picker and emits `checkin` the moment a
 * photo is picked (one step, no separate upload button, matching PhotoPickerComponent's own
 * immediate-emit behavior); a done task shows its check-in photo instead.
 */
@Component({
  selector: 'td-plan-task-card',
  standalone: true,
  imports: [ChipComponent, PhotoPickerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './plan-task-card.component.html',
  styleUrl: './plan-task-card.component.scss',
})
export class PlanTaskCardComponent {
  @Input({ required: true }) task!: Task;
  /** True while a check-in was just posted and the orchestrator's feedback (PA-05) hasn't
   * arrived yet — drives a "Tendril is reviewing your check-in…" line below the photo. */
  @Input() awaitingFeedback = false;
  @Output() checkin = new EventEmitter<File>();

  onPhotoSelected(file: File | null): void {
    if (file) {
      this.checkin.emit(file);
    }
  }
}
