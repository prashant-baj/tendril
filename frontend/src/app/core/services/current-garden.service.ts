import { Injectable, inject, signal } from '@angular/core';
import { toObservable } from '@angular/core/rxjs-interop';
import { HttpErrorResponse } from '@angular/common/http';
import { EMPTY, Observable, catchError, map, shareReplay, switchMap, throwError } from 'rxjs';
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
    switchMap((id) =>
      id
        ? this.gardenApi.getGardenById(id).pipe(catchError((err) => this.recover(id, err)))
        : this.gardenApi.getGarden(),
    ),
    shareReplay({ bufferSize: 1, refCount: true }),
  );

  setCurrentGardenId(gardenId: string): void {
    localStorage.setItem(STORAGE_KEY, gardenId);
    this.gardenIdSignal.set(gardenId);
  }

  /** The saved garden no longer exists (404 — e.g. deleted): switch to the first garden the user
   * still owns, or back to the pre-onboarding state when there are none, instead of silently
   * showing the demo fixture with an id that will never load. Other errors propagate. */
  private recover(staleId: string, err: unknown): Observable<Garden> {
    if (!(err instanceof HttpErrorResponse) || err.status !== 404) {
      return throwError(() => err);
    }
    return this.gardenApi.getGardens().pipe(
      map((gardens) => gardens.find((g) => g.gardenId !== staleId)),
      switchMap((next) => {
        if (next) {
          this.setCurrentGardenId(next.gardenId); // re-emits garden$ with the real garden
        } else {
          localStorage.removeItem(STORAGE_KEY);
          this.gardenIdSignal.set(null);
        }
        return EMPTY;
      }),
    );
  }
}
