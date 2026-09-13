import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { Observable, of } from 'rxjs';
import { GoalDetailComponent } from './goal-detail.component';
import { GoalApi, GoalDetail } from '../../core/services/goal.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';

const DETAIL: GoalDetail = {
  goal: {
    goalId: 'goal-1',
    description: 'Curry leaf plant drying out',
    type: 'diagnosis',
    status: 'PlanProposed',
  },
  plan: {
    planId: 'goal-1',
    goalId: 'goal-1',
    successCriteria: 'Leaves stay green for 2 weeks',
    status: 'PlanProposed',
  },
  tasks: [{ taskId: 't1', title: 'Water deeply', detail: 'Soak the soil', scope: 'plant', status: 'pending' }],
  media: [{ mediaId: 'm1', downloadUrl: 'https://s3.example/photo.jpg' }],
  messages: [{ role: 'user', content: 'help', createdAt: '2026-01-01T00:00:00Z' }],
};

class FakeGoalApi {
  lastSendMessage: { gardenId: string; goalId: string; content: string } | undefined;
  lastApprove: { gardenId: string; planId: string } | undefined;

  getGoals(): Observable<never[]> {
    return of([]);
  }

  getGoalDetail(): Observable<GoalDetail> {
    return of(DETAIL);
  }

  sendMessage(gardenId: string, goalId: string, content: string): Observable<void> {
    this.lastSendMessage = { gardenId, goalId, content };
    return of(undefined);
  }

  approve(gardenId: string, planId: string): Observable<void> {
    this.lastApprove = { gardenId, planId };
    return of(undefined);
  }
}

describe('GoalDetailComponent', () => {
  let fixture: ComponentFixture<GoalDetailComponent>;
  let fakeGoalApi: FakeGoalApi;

  beforeEach(async () => {
    fakeGoalApi = new FakeGoalApi();
    await TestBed.configureTestingModule({
      imports: [GoalDetailComponent],
      providers: [
        provideRouter([]),
        { provide: GoalApi, useValue: fakeGoalApi },
        { provide: CurrentGardenService, useValue: { gardenId: () => 'g-1' } },
        {
          provide: ActivatedRoute,
          useValue: { paramMap: of(convertToParamMap({ goalId: 'goal-1' })) },
        },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(GoalDetailComponent);
    fixture.detectChanges();
  });

  it('renders the goal description, plan, and tasks', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Curry leaf plant drying out');
    expect(text).toContain('Leaves stay green for 2 weeks');
    expect(text).toContain('Water deeply');
  });

  it('renders the attached photo', () => {
    const img: HTMLImageElement | null = fixture.nativeElement.querySelector('img');
    expect(img?.src).toContain('photo.jpg');
  });

  it('renders the chat thread', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('help');
  });

  it('sending a reply calls GoalApi.sendMessage with the garden/goal id and content', () => {
    fixture.componentInstance.replyForm.setValue({ content: 'Can we water less?' });
    fixture.componentInstance.sendMessage();

    expect(fakeGoalApi.lastSendMessage).toEqual({
      gardenId: 'g-1',
      goalId: 'goal-1',
      content: 'Can we water less?',
    });
  });

  it('does not send an empty reply', () => {
    fixture.componentInstance.replyForm.setValue({ content: '' });
    fixture.componentInstance.sendMessage();

    expect(fakeGoalApi.lastSendMessage).toBeUndefined();
  });

  it('approving calls GoalApi.approve with the garden id and plan id', () => {
    fixture.componentInstance.approve();

    expect(fakeGoalApi.lastApprove).toEqual({ gardenId: 'g-1', planId: 'goal-1' });
  });
});

describe('GoalDetailComponent (polling for the orchestrator\'s reply)', () => {
  class PollingFakeGoalApi {
    callCount = 0;

    getGoalDetail(): Observable<GoalDetail> {
      this.callCount += 1;
      // Calls 1-2 (the initial fetch + the poll's immediate first check) still see the
      // original single message; call 3+ (after one interval tick) simulates the orchestrator
      // having written both the echoed user message and its reply by then.
      const messages =
        this.callCount >= 3
          ? [
              ...DETAIL.messages,
              { role: 'user' as const, content: 'more info', createdAt: 'x1' },
              { role: 'assistant' as const, content: 'Got it, adjusting the plan.', createdAt: 'x2' },
            ]
          : DETAIL.messages;
      return of({ ...DETAIL, messages });
    }

    sendMessage(): Observable<void> {
      return of(undefined);
    }

    approve(): Observable<void> {
      return of(undefined);
    }

    getGoals(): Observable<never[]> {
      return of([]);
    }
  }

  it('shows a typing indicator while polling, then the reply once it arrives — no manual refresh needed', fakeAsync(() => {
    TestBed.configureTestingModule({
      imports: [GoalDetailComponent],
      providers: [
        provideRouter([]),
        { provide: GoalApi, useClass: PollingFakeGoalApi },
        { provide: CurrentGardenService, useValue: { gardenId: () => 'g-1' } },
        {
          provide: ActivatedRoute,
          useValue: { paramMap: of(convertToParamMap({ goalId: 'goal-1' })) },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GoalDetailComponent);
    fixture.detectChanges();

    fixture.componentInstance.replyForm.setValue({ content: 'more info' });
    fixture.componentInstance.sendMessage();

    expect(fixture.componentInstance.waitingForReply()).toBe(true);
    expect(fixture.componentInstance.detail()?.messages.length).toBe(1);

    tick(2000);

    expect(fixture.componentInstance.waitingForReply()).toBe(false);
    expect(fixture.componentInstance.detail()?.messages.length).toBe(3);
    expect(fixture.componentInstance.detail()?.messages.at(-1)?.content).toBe(
      'Got it, adjusting the plan.',
    );
  }));
});

describe('GoalDetailComponent (goal not found)', () => {
  it('shows a not-found message when the goal has no detail', async () => {
    await TestBed.configureTestingModule({
      imports: [GoalDetailComponent],
      providers: [
        provideRouter([]),
        {
          provide: GoalApi,
          useValue: { getGoalDetail: () => of(undefined) },
        },
        { provide: CurrentGardenService, useValue: { gardenId: () => 'g-1' } },
        {
          provide: ActivatedRoute,
          useValue: { paramMap: of(convertToParamMap({ goalId: 'missing' })) },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GoalDetailComponent);
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Goal not found');
  });
});
