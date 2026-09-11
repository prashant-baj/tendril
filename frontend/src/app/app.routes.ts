import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'home' },
  {
    path: 'home',
    data: { title: 'Good morning, Meera' },
    loadComponent: () => import('./features/home/home.component').then((m) => m.HomeComponent),
  },
  {
    path: 'capture',
    data: { title: 'Check a plant' },
    loadComponent: () => import('./features/capture/capture.component').then((m) => m.CaptureComponent),
  },
  {
    path: 'goals/:goalId',
    data: { title: 'Goal' },
    loadComponent: () => import('./features/goal-detail/goal-detail.component').then((m) => m.GoalDetailComponent),
  },
  {
    path: 'tasks',
    data: { title: 'Tasks' },
    loadComponent: () => import('./features/tasks/tasks.component').then((m) => m.TasksComponent),
  },
  {
    path: 'garden',
    data: { title: 'Your garden' },
    loadComponent: () => import('./features/garden/garden.component').then((m) => m.GardenComponent),
  },
  {
    path: 'activity',
    data: { title: 'Activity' },
    loadComponent: () => import('./features/activity/activity.component').then((m) => m.ActivityComponent),
  },
  { path: '**', redirectTo: 'home' },
];
