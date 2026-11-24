import { NavItem } from '../core/models/nav.model';

/** The primary destinations shown in both the desktop rail and the mobile bottom nav. */
export const NAV_ITEMS: NavItem[] = [
  { id: 'home', route: '/home', icon: 'home', label: 'Home' },
  { id: 'garden', route: '/garden', icon: 'yard', label: 'Garden' },
  // ADR-0038: planned and recurring farm work, with the Supervisor's assessments.
  { id: 'farm', route: '/farm-work', icon: 'agriculture', label: 'Farm work' },
  { id: 'tasks', route: '/tasks', icon: 'task_alt', label: 'Tasks' },
  { id: 'activity', route: '/activity', icon: 'history', label: 'Activity' },
];
