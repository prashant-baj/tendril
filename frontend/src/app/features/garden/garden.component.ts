import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { PlantRowComponent } from '../../shared/components/plant-row/plant-row.component';
import { GardenApi } from '../../core/services/garden.service';

@Component({
  selector: 'td-garden',
  standalone: true,
  imports: [IconComponent, PlantRowComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './garden.component.html',
  styleUrl: './garden.component.scss',
})
export class GardenComponent {
  private readonly gardenApi = inject(GardenApi);
  private readonly router = inject(Router);

  readonly garden = toSignal(this.gardenApi.getGarden(), { initialValue: undefined });
  readonly gardenFacts = toSignal(this.gardenApi.getGardenFacts(), { initialValue: [] });
  readonly plants = toSignal(this.gardenApi.getPlants(), { initialValue: [] });

  // No per-plant detail screen yet — matches the mockup, which sends every plant row into
  // the capture flow rather than a dedicated plant page.
  openCapture(): void {
    this.router.navigateByUrl('/capture');
  }
}
