import { ComponentFixture, TestBed } from '@angular/core/testing';
import { TasksComponent } from './tasks.component';
import { TaskApi, MockTaskApi } from '../../core/services/task.service';

describe('TasksComponent', () => {
  let fixture: ComponentFixture<TasksComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TasksComponent],
      providers: [{ provide: TaskApi, useClass: MockTaskApi }],
    }).compileComponents();
    fixture = TestBed.createComponent(TasksComponent);
    fixture.detectChanges();
  });

  it('renders all three groups', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Today');
    expect(text).toContain('This week');
    expect(text).toContain('Later');
  });
});
