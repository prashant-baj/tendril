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

  it('renders the proposal banner, today tasks, goals, and plants', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Waiting on you');
    expect(text).toContain('Hand-pollinate tomato flowers');
    expect(text).toContain('Get Tomato #2 setting fruit within 3 weeks');
    expect(text).toContain('Tomato #2');
  });

  it('toggling a today task delegates to TaskApi', () => {
    const taskApi = TestBed.inject(TaskApi);
    spyOn(taskApi, 'toggle');
    fixture.componentInstance.toggleTask('t2');
    expect(taskApi.toggle).toHaveBeenCalledWith('t2');
  });
});
