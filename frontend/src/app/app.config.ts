import { ApplicationConfig, provideZoneChangeDetection, isDevMode } from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { provideServiceWorker } from '@angular/service-worker';

import { routes } from './app.routes';
import { userIdInterceptor } from './core/interceptors/user-id.interceptor';
import { GardenApi, HttpGardenApi } from './core/services/garden.service';
import { GoalApi, HttpGoalApi } from './core/services/goal.service';
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
    // Mock data layer (no backend yet — ADR-0004) except GardenApi/GoalApi, which now have a
    // real Client API (OB-01/PA-01/PA-02) — bound to their Http* implementations, which
    // themselves delegate to a Mock* fallback only for the pre-onboarding (no garden yet) case.
    // Swap the rest's `useClass` the same way as their stories land.
    { provide: GardenApi, useClass: HttpGardenApi },
    { provide: GoalApi, useClass: HttpGoalApi },
    { provide: TaskApi, useClass: MockTaskApi },
    { provide: ActivityApi, useClass: MockActivityApi },
  ],
};
