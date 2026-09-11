import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { IconComponent } from '../icon/icon.component';
import { Plant } from '../../../core/models/plant.model';
import { plantHealthLabel, plantHealthTone } from '../plant-health.util';

/** A plant tile in the Home screen's horizontally-scrolling plant strip. */
@Component({
  selector: 'td-plant-card',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './plant-card.component.html',
  styleUrl: './plant-card.component.scss',
})
export class PlantCardComponent {
  @Input({ required: true }) plant!: Plant;
  @Output() activate = new EventEmitter<void>();

  get healthLabel(): string {
    return plantHealthLabel(this.plant.healthState);
  }

  get dotTone(): string {
    const tone = plantHealthTone(this.plant.healthState);
    return `dot-${tone}`;
  }
}
