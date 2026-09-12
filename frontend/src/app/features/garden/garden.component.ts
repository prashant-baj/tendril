import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { PlantRowComponent } from '../../shared/components/plant-row/plant-row.component';
import { GardenApi } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';

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
  private readonly currentGarden = inject(CurrentGardenService);
  private readonly router = inject(Router);

  // The real created garden (name/geolocation/vision). `plants()` reflects real plants added
  // via OB-02 (prepended onto the still-mocked base list, HttpGardenApi.createPlant) —
  // `gardenFacts` (sun hours, container type, etc.) stays mocked; no story computes those yet.
  readonly garden = toSignal(this.currentGarden.garden$, { initialValue: undefined });
  readonly gardenFacts = toSignal(this.gardenApi.getGardenFacts(), { initialValue: [] });
  readonly plants = toSignal(this.gardenApi.getPlants(), { initialValue: [] });

  // No per-plant detail screen yet — matches the mockup, which sends every plant row into
  // the capture flow rather than a dedicated plant page.
  openCapture(): void {
    this.router.navigateByUrl('/capture');
  }

  openAddPlant(): void {
    this.router.navigateByUrl('/add-plant');
  }
}
