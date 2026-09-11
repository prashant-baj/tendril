import { PlantHealthState } from '../../core/models/plant.model';
import { ChipTone } from './chip/chip.component';

/** Shared healthState → presentation mapping for plant-card and plant-row. */
export function plantHealthTone(state: PlantHealthState): ChipTone {
  switch (state) {
    case 'healthy': return 'green';
    case 'needs-care': return 'amber';
    case 'watch': return 'neutral';
  }
}

export function plantHealthLabel(state: PlantHealthState): string {
  switch (state) {
    case 'healthy': return 'Healthy';
    case 'needs-care': return 'Needs care';
    case 'watch': return 'Watch';
  }
}

export function plantHealthIcon(state: PlantHealthState): string {
  switch (state) {
    case 'healthy': return 'check_circle';
    case 'needs-care': return 'pending';
    case 'watch': return 'visibility';
  }
}
