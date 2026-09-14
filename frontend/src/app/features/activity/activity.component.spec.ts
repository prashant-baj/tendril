import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { ActivityComponent } from './activity.component';
import { ActivityApi, MockActivityApi } from '../../core/services/activity.service';
import { ActivityEvent } from '../../core/models/activity.model';
import { GardenApi, MockGardenApi } from '../../core/services/garden.service';

describe('ActivityComponent', () => {
  let fixture: ComponentFixture<ActivityComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ActivityComponent],
      providers: [
        provideRouter([]),
        { provide: ActivityApi, useClass: MockActivityApi },
        { provide: GardenApi, useClass: MockGardenApi },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(ActivityComponent);
    fixture.detectChanges();
  });

  it('shows an empty state when there is no activity yet', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Nothing has happened yet');
  });
});

describe('ActivityComponent with real events', () => {
  let fixture: ComponentFixture<ActivityComponent>;

  const events: ActivityEvent[] = [
    {
      type: 'goal.submitted',
      payload: { goalId: 'goal-1', description: 'Leaves turning yellow' },
      createdAt: '2026-01-01T00:00:00+00:00',
      goalId: 'goal-1',
    },
  ];

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ActivityComponent],
      providers: [
        provideRouter([]),
        { provide: ActivityApi, useValue: { getActivity: () => of(events) } },
        { provide: GardenApi, useClass: MockGardenApi },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(ActivityComponent);
    fixture.detectChanges();
  });

  it('renders a real event through to the presentation layer', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('New issue reported');
    expect(text).toContain('Leaves turning yellow');
  });
});
