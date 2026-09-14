import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { TasksComponent } from './tasks.component';
import { TaskApi, MockTaskApi } from '../../core/services/task.service';
import { GardenTaskItem } from '../../core/models/task.model';
import { GardenApi, MockGardenApi } from '../../core/services/garden.service';

describe('TasksComponent (empty)', () => {
  let fixture: ComponentFixture<TasksComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TasksComponent],
      providers: [
        provideRouter([]),
        { provide: TaskApi, useClass: MockTaskApi },
        { provide: GardenApi, useClass: MockGardenApi },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(TasksComponent);
    fixture.detectChanges();
  });

  it('renders both groups with an empty state', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('To do');
    expect(text).toContain('Done');
    expect(text).toContain('Nothing here yet.');
  });
});

describe('TasksComponent (real data)', () => {
  let fixture: ComponentFixture<TasksComponent>;

  const tasks: GardenTaskItem[] = [
    { taskId: 't1', goalId: 'g1', title: 'Water deeply', detail: 'd', scope: 'plant', status: 'pending' },
    { taskId: 't2', goalId: 'g1', title: 'Mulch', detail: 'd', scope: 'plant', status: 'done' },
  ];

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TasksComponent],
      providers: [
        provideRouter([]),
        { provide: TaskApi, useValue: { getTasks: () => of(tasks) } },
        { provide: GardenApi, useClass: MockGardenApi },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(TasksComponent);
    fixture.detectChanges();
  });

  it('groups tasks by status: To do / Done', () => {
    const groups = fixture.componentInstance.taskGroups();
    expect(groups[0]).toEqual({ title: 'To do', items: [tasks[0]] });
    expect(groups[1]).toEqual({ title: 'Done', items: [tasks[1]] });
  });

  it('renders both tasks', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Water deeply');
    expect(text).toContain('Mulch');
  });
});
