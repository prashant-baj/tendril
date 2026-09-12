import { Injectable, signal } from '@angular/core';

const STORAGE_KEY = 'tendril:currentGardenId';

/**
 * Tracks the single "current garden" for the app to operate on (OB-01: no garden-switcher UI
 * yet — just enough state so screens created after "Setup My Garden" have a garden to attach
 * to). Persisted in localStorage so a page reload doesn't lose it.
 */
@Injectable({ providedIn: 'root' })
export class CurrentGardenService {
  private readonly gardenIdSignal = signal<string | null>(localStorage.getItem(STORAGE_KEY));

  readonly gardenId = this.gardenIdSignal.asReadonly();

  setCurrentGardenId(gardenId: string): void {
    localStorage.setItem(STORAGE_KEY, gardenId);
    this.gardenIdSignal.set(gardenId);
  }
}
