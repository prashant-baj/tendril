import { ComponentFixture, TestBed } from '@angular/core/testing';
import { TaskRowComponent } from './task-row.component';
import { TaskItem } from '../../../core/models/task.model';

describe('TaskRowComponent', () => {
  let fixture: ComponentFixture<TaskRowComponent>;
  const task: TaskItem = {
    taskId: 't1',
    label: 'Hand-pollinate tomato flowers',
    meta: 'Tomato #2 · 7:00 AM',
    metaIcon: 'potted_plant',
    chip: '7:00 AM',
    tone: 'due',
    done: false,
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [TaskRowComponent] }).compileComponents();
    fixture = TestBed.createComponent(TaskRowComponent);
    fixture.componentRef.setInput('task', task);
    fixture.detectChanges();
  });

  it('renders the task label', () => {
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('Hand-pollinate tomato flowers');
  });

  it('emits toggle with the task id when clicked', () => {
    const emitted: string[] = [];
    fixture.componentInstance.toggle.subscribe((id: string) => emitted.push(id));
    (fixture.nativeElement as HTMLElement).querySelector('button')!.click();
    expect(emitted).toEqual(['t1']);
  });

  it('shows a strikethrough label and a green "Done" chip once completed', () => {
    fixture.componentRef.setInput('task', { ...task, done: true, chip: 'Done' });
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('.label.done')).toBeTruthy();
    expect(el.textContent).toContain('Done');
  });
});
