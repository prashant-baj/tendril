import { ApplicationConfig, provideZoneChangeDetection, isDevMode } from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { provideServiceWorker } from '@angular/service-worker';

import { routes } from './app.routes';
import { userIdInterceptor } from './core/interceptors/user-id.interceptor';
import { GardenApi, HttpGardenApi } from './core/services/garden.service';
import { GoalApi, HttpGoalApi } from './core/services/goal.service';
import { TaskApi, HttpTaskApi } from './core/services/task.service';
import { ActivityApi, HttpActivityApi } from './core/services/activity.service';

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
    // Every Api is now bound to a real Http* implementation (GardenApi/GoalApi since
    // OB-01/PA-01/PA-02, TaskApi/ActivityApi since Phase 6) — each still delegates to a Mock*
    // fallback only for the pre-onboarding (no garden yet) case.
    { provide: GardenApi, useClass: HttpGardenApi },
    { provide: GoalApi, useClass: HttpGoalApi },
    { provide: TaskApi, useClass: HttpTaskApi },
    { provide: ActivityApi, useClass: HttpActivityApi },
  ],
};
