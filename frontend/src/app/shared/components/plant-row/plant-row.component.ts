import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { IconComponent } from '../icon/icon.component';
import { ChipComponent } from '../chip/chip.component';
import { Plant } from '../../../core/models/plant.model';
import { plantHealthIcon, plantHealthLabel, plantHealthTone } from '../plant-health.util';

/** One plant row on the Garden screen's plant list. */
@Component({
  selector: 'td-plant-row',
  standalone: true,
  imports: [IconComponent, ChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './plant-row.component.html',
  styleUrl: './plant-row.component.scss',
})
export class PlantRowComponent {
  @Input({ required: true }) plant!: Plant;
  @Output() activate = new EventEmitter<void>();

  get healthLabel(): string {
    return plantHealthLabel(this.plant.healthState);
  }
  get healthIcon(): string {
    return plantHealthIcon(this.plant.healthState);
  }
  get healthTone() {
    return plantHealthTone(this.plant.healthState);
  }
}
