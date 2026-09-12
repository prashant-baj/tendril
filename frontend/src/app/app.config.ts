import { ApplicationConfig, provideZoneChangeDetection, isDevMode } from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { provideServiceWorker } from '@angular/service-worker';

import { routes } from './app.routes';
import { userIdInterceptor } from './core/interceptors/user-id.interceptor';
import { GardenApi, HttpGardenApi } from './core/services/garden.service';
import { GoalApi, MockGoalApi } from './core/services/goal.service';
import { TaskApi, MockTaskApi } from './core/services/task.service';
import { ActivityApi, MockActivityApi } from './core/services/activity.service';

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes),
    provideHttpClient(withInterceptors([userIdInterceptor])),
    provideAnimationsAsync(),
    provideServiceWorker('ngsw-worker.js', {
      enabled: !isDevMode(),
      registrationStrategy: 'registerWhenStable:30000',
    }),
    // Mock data layer (no backend yet — ADR-0004) except GardenApi: OB-01 stood up the real
    // Client API for createGarden/getGardenById, so it's bound to HttpGardenApi (which itself
    // delegates the still-mocked reads to MockGardenApi). Swap the rest's `useClass` the same
    // way as their stories land.
    { provide: GardenApi, useClass: HttpGardenApi },
    { provide: GoalApi, useClass: MockGoalApi },
    { provide: TaskApi, useClass: MockTaskApi },
    { provide: ActivityApi, useClass: MockActivityApi },
  ],
};
