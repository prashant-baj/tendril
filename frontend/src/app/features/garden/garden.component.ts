import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { toObservable, toSignal } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';
import { BehaviorSubject, combineLatest } from 'rxjs';
import { switchMap } from 'rxjs/operators';
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

  // Bumped after a delete so plants() re-fetches — there's no push/websocket channel for this
  // yet, so a manual refresh trigger is the simplest way to reflect a mutation immediately.
  private readonly refresh$ = new BehaviorSubject<void>(undefined);

  // The real created garden (name/geolocation/vision). `plants()` now reads real plants from
  // `GET /gardens/{gardenId}/plants` — `gardenFacts` (sun hours, container type, etc.) stays
  // mocked; no story computes those yet.
  readonly garden = toSignal(this.currentGarden.garden$, { initialValue: undefined });
  readonly gardenFacts = toSignal(this.gardenApi.getGardenFacts(), { initialValue: [] });
  readonly plants = toSignal(
    combineLatest([toObservable(this.currentGarden.gardenId), this.refresh$]).pipe(
      switchMap(([gardenId]) => this.gardenApi.getPlants(gardenId)),
    ),
    { initialValue: [] },
  );

  // No per-plant detail screen yet — matches the mockup, which sends every plant row into
  // the capture flow rather than a dedicated plant page.
  openCapture(): void {
    this.router.navigateByUrl('/capture');
  }

  openAddPlant(): void {
    this.router.navigateByUrl('/add-plant');
  }

  deletePlant(plantId: string): void {
    const gardenId = this.currentGarden.gardenId();
    if (!gardenId) {
      return;
    }
    this.gardenApi.deletePlant(gardenId, plantId).subscribe(() => this.refresh$.next());
  }
}
