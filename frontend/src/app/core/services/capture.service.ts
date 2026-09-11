import { Injectable, computed, signal } from '@angular/core';
import { CapturePhase } from '../models/capture.model';
import { LiveStep } from '../models/trace.model';

/**
 * The first 5 specialists from the goal's decision trace, in reveal order. Mock-only:
 * duplicated from `MockGoalApi`'s trace fixture (minus the orchestrator) rather than shared,
 * since a real implementation replaces this whole service with a live orchestrator/WebSocket
 * stream (ADR-0004) rather than reusing the goal-detail trace data.
 */
const SPECIALISTS: Array<{ name: string; icon: string; note: string }> = [
  { name: 'Vision & diagnosis', icon: 'photo_camera', note: 'Lower-leaf chlorosis; flowers intact, no pest damage.' },
  { name: 'Agronomy', icon: 'science', note: 'Nitrogen is high for a fruiting plant — move to a high-potassium feed.' },
  { name: 'Weather', icon: 'thermostat', note: '34 °C spell through Thursday, then dry Friday to Sunday.' },
  { name: 'Pollination', icon: 'hive', note: 'A 3rd-floor balcony gets almost no pollinators — hand-pollinate daily.' },
  { name: 'Irrigation', icon: 'water_drop', note: 'Deep-water alternate mornings; mulch the pots to hold moisture.' },
];

const REVEAL_INTERVAL_MS = 900;

/**
 * Drives the capture/diagnose screen's `idle → analyzing → found` phase machine — mocked, in
 * place of a real orchestrator call. Mirrors the mockup's `runAnalysis()` timer exactly
 * (one specialist revealed every 900ms, 5 steps, then "found").
 */
@Injectable({ providedIn: 'root' })
export class CaptureService {
  private readonly _phase = signal<CapturePhase>('idle');
  private readonly _liveIndex = signal(0);
  private timer?: ReturnType<typeof setInterval>;

  readonly phase = this._phase.asReadonly();

  readonly liveSteps = computed<LiveStep[]>(() => {
    const liveIndex = this._liveIndex();
    return SPECIALISTS.map((s, i) => {
      const state = i < liveIndex ? 'done' : i === liveIndex ? 'active' : 'todo';
      return {
        name: s.name,
        icon: state === 'done' ? 'check_circle' : s.icon,
        note: state === 'todo' ? 'Queued' : s.note,
        state,
      };
    });
  });

  runAnalysis(): void {
    this.clearTimer();
    this._phase.set('analyzing');
    this._liveIndex.set(0);
    this.timer = setInterval(() => {
      const next = this._liveIndex() + 1;
      if (next >= SPECIALISTS.length) {
        this.clearTimer();
        this._liveIndex.set(SPECIALISTS.length);
        this._phase.set('found');
      } else {
        this._liveIndex.set(next);
      }
    }, REVEAL_INTERVAL_MS);
  }

  reset(): void {
    this.clearTimer();
    this._phase.set('idle');
    this._liveIndex.set(0);
  }

  private clearTimer(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = undefined;
  }
}
