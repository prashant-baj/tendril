import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { HomeComponent } from './home.component';
import { GardenApi, MockGardenApi } from '../../core/services/garden.service';
import { GoalApi, MockGoalApi } from '../../core/services/goal.service';
import { TaskApi, MockTaskApi } from '../../core/services/task.service';

describe('HomeComponent', () => {
  let fixture: ComponentFixture<HomeComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HomeComponent],
      providers: [
        provideRouter([]),
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

  it('toggling a today task delegates to TaskApi', () => {
    const taskApi = TestBed.inject(TaskApi);
    spyOn(taskApi, 'toggle');
    fixture.componentInstance.toggleTask('t2');
    expect(taskApi.toggle).toHaveBeenCalledWith('t2');
  });
});
