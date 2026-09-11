import { ApplicationConfig, provideZoneChangeDetection, isDevMode } from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { provideServiceWorker } from '@angular/service-worker';

import { routes } from './app.routes';
import { GardenApi, MockGardenApi } from './core/services/garden.service';
import { GoalApi, MockGoalApi } from './core/services/goal.service';
import { TaskApi, MockTaskApi } from './core/services/task.service';
import { ActivityApi, MockActivityApi } from './core/services/activity.service';

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes),
    provideAnimationsAsync(),
    provideServiceWorker('ngsw-worker.js', {
      enabled: !isDevMode(),
      registrationStrategy: 'registerWhenStable:30000',
    }),
    // Mock data layer (no backend yet — ADR-0004). Swap the `useClass` to a real
    // Http-backed implementation later without touching any component.
    { provide: GardenApi, useClass: MockGardenApi },
    { provide: GoalApi, useClass: MockGoalApi },
    { provide: TaskApi, useClass: MockTaskApi },
    { provide: ActivityApi, useClass: MockActivityApi },
  ],
};
