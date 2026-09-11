import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { GoalDetailComponent } from './goal-detail.component';
import { GoalApi, GoalDetail } from '../../core/services/goal.service';

const DETAIL: GoalDetail = {
  goal: {
    goalId: 'g1',
    title: 'Get Tomato #2 setting fruit within 3 weeks',
    garden: 'Balcony Kitchen Garden',
    status: 'in-progress',
    statusLabel: 'In progress',
    statusIcon: 'pending',
    timing: 'Day 5 of 21',
    next: 'Next check-in tomorrow',
    progressPct: 62,
    successCriteria: 'at least 5 fruits have set',
  },
  stats: [{ value: '18', label: 'days' }],
  trace: [{ agent: 'Vision & diagnosis', icon: 'photo_camera', says: 'Looks fine.', ms: '1.4s' }],
  planTasks: [],
  followUps: [],
};

class FakeGoalApi {
  getGoalDetail() {
    return of(DETAIL);
  }
}

describe('GoalDetailComponent', () => {
  let fixture: ComponentFixture<GoalDetailComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GoalDetailComponent],
      providers: [
        provideRouter([]),
        { provide: GoalApi, useClass: FakeGoalApi },
        {
          provide: ActivatedRoute,
          useValue: { paramMap: of(convertToParamMap({ goalId: 'g1' })) },
        },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(GoalDetailComponent);
    fixture.detectChanges();
  });

  it('renders the goal title', () => {
    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Get Tomato #2 setting fruit within 3 weeks',
    );
  });

  it('the trace is collapsed by default and opens on toggle', () => {
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).not.toContain('Vision & diagnosis');

    fixture.componentInstance.toggleTrace();
    fixture.detectChanges();
    expect(el.textContent).toContain('Vision & diagnosis');

    fixture.componentInstance.toggleTrace();
    fixture.detectChanges();
    expect(el.textContent).not.toContain('Vision & diagnosis');
  });
});
