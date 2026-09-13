import { ComponentFixture, TestBed } from '@angular/core/testing';
import { PlanTaskCardComponent } from './plan-task-card.component';
import { Task } from '../../../core/models/plan.model';

describe('PlanTaskCardComponent', () => {
  let fixture: ComponentFixture<PlanTaskCardComponent>;
  let component: PlanTaskCardComponent;

  const pendingTask: Task = {
    taskId: 'task-1',
    title: 'Check soil moisture',
    detail: 'Water if the top inch feels dry.',
    scope: 'plant',
    status: 'pending',
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PlanTaskCardComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(PlanTaskCardComponent);
    component = fixture.componentInstance;
  });

  it('shows a photo picker and no "Done" chip for a pending task', () => {
    component.task = pendingTask;
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('td-photo-picker')).toBeTruthy();
    expect(el.textContent).not.toContain('Done');
  });

  it('emits checkin the moment a photo is picked', () => {
    component.task = pendingTask;
    fixture.detectChanges();

    let emitted: File | undefined;
    component.checkin.subscribe((f) => (emitted = f));

    const file = new File(['x'], 'proof.jpg', { type: 'image/jpeg' });
    component.onPhotoSelected(file);

    expect(emitted).toBe(file);
  });

  it('does not emit when the photo picker clears its selection', () => {
    component.task = pendingTask;
    fixture.detectChanges();

    let emitted = false;
    component.checkin.subscribe(() => (emitted = true));

    component.onPhotoSelected(null);

    expect(emitted).toBe(false);
  });

  it('shows the check-in photo and a "Done" chip for a completed task, no picker', () => {
    component.task = {
      ...pendingTask,
      status: 'done',
      media: { mediaId: 'media-1', downloadUrl: 'https://example.test/photo.jpg' },
    };
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Done');
    expect(el.querySelector('td-photo-picker')).toBeFalsy();
    const img = el.querySelector('.checkin-photo') as HTMLImageElement;
    expect(img).toBeTruthy();
    expect(img.src).toBe('https://example.test/photo.jpg');
  });

  it('renders the agent feedback below the check-in photo once it arrives', () => {
    component.task = {
      ...pendingTask,
      status: 'done',
      media: { mediaId: 'media-1', downloadUrl: 'https://example.test/photo.jpg' },
      feedback: 'Looking good, keep it up!',
    };
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.feedback')?.textContent).toContain('Looking good, keep it up!');
  });

  it('shows a waiting line while awaitingFeedback is true and no feedback has arrived yet', () => {
    component.task = {
      ...pendingTask,
      status: 'done',
      media: { mediaId: 'media-1', downloadUrl: 'https://example.test/photo.jpg' },
    };
    component.awaitingFeedback = true;
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.feedback.pending')?.textContent).toContain(
      'Tendril is reviewing your check-in',
    );
  });

  it('prefers rendering feedback over the waiting line once feedback has arrived', () => {
    component.task = {
      ...pendingTask,
      status: 'done',
      media: { mediaId: 'media-1', downloadUrl: 'https://example.test/photo.jpg' },
      feedback: 'Looking good!',
    };
    component.awaitingFeedback = true;
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.feedback.pending')).toBeFalsy();
    expect(el.querySelector('.feedback')?.textContent).toContain('Looking good!');
  });
});
