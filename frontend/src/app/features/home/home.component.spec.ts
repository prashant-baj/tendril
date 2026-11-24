import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { HomeComponent } from './home.component';
import { GardenApi, MockGardenApi } from '../../core/services/garden.service';
import { GoalApi, MockGoalApi } from '../../core/services/goal.service';
import { TaskApi, MockTaskApi } from '../../core/services/task.service';
import { Goal } from '../../core/models/goal.model';
import { GardenTaskItem } from '../../core/models/task.model';
import { FarmWorkApi } from '../../core/services/farm-work.service';

describe('HomeComponent', () => {
  let fixture: ComponentFixture<HomeComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HomeComponent],
      providers: [
        provideRouter([]),
        { provide: FarmWorkApi, useValue: { listActivities: () => of([]) } },
        { provide: GardenApi, useClass: MockGardenApi },
        { provide: GoalApi, useClass: MockGoalApi },
        { provide: TaskApi, useClass: MockTaskApi },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(HomeComponent);
    fixture.detectChanges();
  });

  it('renders without error when tasks, goals, and plants are all empty', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Today');
    expect(text).toContain('Goals in progress');
    expect(text).toContain('Plants');
  });
});

describe('HomeComponent today tasks', () => {
  let fixture: ComponentFixture<HomeComponent>;

  const tasks: GardenTaskItem[] = [
    { taskId: 't1', goalId: 'g1', title: 'Water deeply', detail: 'd', scope: 'plant', status: 'pending' },
    { taskId: 't2', goalId: 'g1', title: 'Already done', detail: 'd', scope: 'plant', status: 'done' },
  ];

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HomeComponent],
      providers: [
        provideRouter([]),
        { provide: FarmWorkApi, useValue: { listActivities: () => of([]) } },
        { provide: GardenApi, useClass: MockGardenApi },
        { provide: GoalApi, useClass: MockGoalApi },
        { provide: TaskApi, useValue: { getTasks: () => of(tasks) } },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(HomeComponent);
    fixture.detectChanges();
  });

  it('shows only not-yet-done tasks in the Today widget', () => {
    expect(fixture.componentInstance.todayTasks()).toEqual([tasks[0]]);
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Water deeply');
    expect(text).not.toContain('Already done');
  });
});

describe('HomeComponent goals split', () => {
  let fixture: ComponentFixture<HomeComponent>;

  const goals: Goal[] = [
    { goalId: 'g1', description: 'Approved goal', type: 'diagnosis', status: 'Approved' },
    { goalId: 'g2', description: 'Awaiting approval', type: 'diagnosis', status: 'PlanProposed' },
    // ADR-0036: a saved check-in needs nothing from the gardener and isn't in progress either.
    { goalId: 'g3', description: 'Initial check in', type: 'diagnosis', status: 'InformationSaved' },
  ];

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HomeComponent],
      providers: [
        provideRouter([]),
        { provide: FarmWorkApi, useValue: { listActivities: () => of([]) } },
        { provide: GardenApi, useClass: MockGardenApi },
        { provide: GoalApi, useValue: { getGoals: () => of(goals) } },
        { provide: TaskApi, useClass: MockTaskApi },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(HomeComponent);
    fixture.detectChanges();
  });

  it('splits goals into "Goals in progress" and "Needs your attention"', () => {
    expect(fixture.componentInstance.goalsInProgress()).toEqual([goals[0]]);
    expect(fixture.componentInstance.goalsNeedingAttention()).toEqual([goals[1]]);
    // A saved check-in has its own section rather than disappearing (ADR-0036).
    expect(fixture.componentInstance.savedGoals()).toEqual([goals[2]]);
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Needs your attention');
    expect(text).toContain('Approved goal');
    expect(text).toContain('Awaiting approval');
  });
});

describe('HomeComponent (farm work due, ADR-0038)', () => {
  afterEach(() => localStorage.removeItem('tendril:currentGardenId'));

  it('lists overdue and due farm work, not upcoming', async () => {
    localStorage.setItem('tendril:currentGardenId', 'g-1');
    const activities = [
      { activityId: 'a1', title: 'Humic drench', kind: 'input', targets: 'all', status: 'active', dueState: 'overdue', overdueOn: '2026-11-20', source: 'user' },
      { activityId: 'a2', title: 'Leaching', kind: 'operation', targets: 'all', status: 'active', dueState: 'due', source: 'user' },
      { activityId: 'a3', title: 'Weeding', kind: 'operation', targets: 'all', status: 'active', dueState: 'upcoming', source: 'user' },
    ];
    await TestBed.configureTestingModule({
      imports: [HomeComponent],
      providers: [
        provideRouter([]),
        { provide: FarmWorkApi, useValue: { listActivities: () => of(activities) } },
        { provide: GardenApi, useClass: MockGardenApi },
        { provide: GoalApi, useClass: MockGoalApi },
        { provide: TaskApi, useClass: MockTaskApi },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(HomeComponent);
    fixture.detectChanges();
    expect(fixture.componentInstance.workDue().map((a) => a.activityId)).toEqual(['a1', 'a2']);
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Farm work due');
    expect(text).toContain('Overdue since 20 Nov 2026');
  });
});
