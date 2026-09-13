import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { TaskRowComponent } from './task-row.component';
import { GardenTaskItem } from '../../../core/models/task.model';

describe('TaskRowComponent', () => {
  let fixture: ComponentFixture<TaskRowComponent>;
  const task: GardenTaskItem = {
    taskId: 't1',
    goalId: 'goal-1',
    title: 'Water deeply',
    detail: 'Soak the soil until drainage runs clear',
    scope: 'plant',
    status: 'pending',
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TaskRowComponent],
      providers: [provideRouter([])],
    }).compileComponents();
    fixture = TestBed.createComponent(TaskRowComponent);
    fixture.componentRef.setInput('task', task);
    fixture.detectChanges();
  });

  it('renders the task title and detail', () => {
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('Water deeply');
    expect(el.textContent).toContain('Soak the soil until drainage runs clear');
  });

  it('shows the scope as a chip when pending, no "Done" text', () => {
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('plant');
    expect(el.textContent).not.toContain('Done');
  });

  it('links to the task\'s own goal', () => {
    const anchor: HTMLAnchorElement = fixture.nativeElement.querySelector('a');
    expect(anchor.getAttribute('href')).toBe('/goals/goal-1');
  });

  it('shows a strikethrough label and a "Done" chip once completed', () => {
    fixture.componentRef.setInput('task', { ...task, status: 'done' });
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('.label.done')).toBeTruthy();
    expect(el.textContent).toContain('Done');
  });
});
