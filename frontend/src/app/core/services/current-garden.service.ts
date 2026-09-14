import { Injectable, inject, signal } from '@angular/core';
import { toObservable } from '@angular/core/rxjs-interop';
import { Observable, shareReplay, switchMap } from 'rxjs';
import { Garden } from '../models/garden.model';
import { GardenApi } from './garden.service';

const STORAGE_KEY = 'tendril:currentGardenId';

/**
 * Tracks the single "current garden" for the app to operate on (OB-01: no garden-switcher UI
 * yet — just enough state so screens created after "Setup My Garden" have a garden to attach
 * to). Persisted in localStorage so a page reload doesn't lose it.
 *
 * `garden$` is the one place every screen should read "the current garden" from (Home's top
 * bar, the Garden screen, etc.) instead of calling `GardenApi.getGarden()` directly — it
 * resolves the *real* created garden once one exists, falling back to `getGarden()`'s
 * pre-onboarding demo fixture for anyone who hasn't been through "Setup My Garden" yet.
 */
@Injectable({ providedIn: 'root' })
export class CurrentGardenService {
  private readonly gardenApi = inject(GardenApi);
  private readonly gardenIdSignal = signal<string | null>(localStorage.getItem(STORAGE_KEY));

  readonly gardenId = this.gardenIdSignal.asReadonly();

  readonly garden$: Observable<Garden> = toObservable(this.gardenIdSignal).pipe(
    switchMap((id) => (id ? this.gardenApi.getGardenById(id) : this.gardenApi.getGarden())),
    shareReplay({ bufferSize: 1, refCount: true }),
  );

  setCurrentGardenId(gardenId: string): void {
    localStorage.setItem(STORAGE_KEY, gardenId);
    this.gardenIdSignal.set(gardenId);
  }
}
