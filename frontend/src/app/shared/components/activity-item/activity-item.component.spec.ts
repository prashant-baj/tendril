import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ActivityItemComponent } from './activity-item.component';
import { ActivityEvent } from '../../../core/models/activity.model';

describe('ActivityItemComponent', () => {
  let fixture: ComponentFixture<ActivityItemComponent>;

  const event: ActivityEvent = {
    type: 'task.checkin',
    payload: { goalId: 'goal-1', taskId: 't1', taskTitle: 'Water deeply' },
    createdAt: '2026-01-01T12:00:00+00:00',
    goalId: 'goal-1',
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ActivityItemComponent],
      providers: [provideRouter([])],
    }).compileComponents();
    fixture = TestBed.createComponent(ActivityItemComponent);
    fixture.componentRef.setInput('event', event);
    fixture.detectChanges();
  });

  it('renders the mapped title and detail for a known event type', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Task checked in');
    expect(text).toContain('Water deeply');
  });

  it('links the title to the event\'s goal when goalId is present', () => {
    const anchor: HTMLAnchorElement | null = fixture.nativeElement.querySelector('a.title');
    expect(anchor?.getAttribute('href')).toBe('/goals/goal-1');
  });

  it('renders a plain (non-linked) title when goalId is absent', () => {
    fixture.componentRef.setInput('event', { ...event, goalId: undefined });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('a.title')).toBeFalsy();
    expect(fixture.nativeElement.querySelector('div.title')).toBeTruthy();
  });

  it('falls back to a generic presentation for an unknown event type', () => {
    fixture.componentRef.setInput('event', {
      type: 'something.new',
      payload: {},
      createdAt: '2026-01-01T00:00:00+00:00',
    });
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('something.new');
  });

  it('hides the connecting line for the last entry', () => {
    fixture.componentRef.setInput('last', true);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.line')?.classList.contains('hidden')).toBe(true);
  });
});
