import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { Observable, of } from 'rxjs';
import { GoalDetailComponent } from './goal-detail.component';
import { GoalApi, GoalDetail } from '../../core/services/goal.service';
import { GardenApi } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';

class FakeGardenApi {
  requestMediaUpload(): Observable<{ uploadUrl: string; mediaId: string }> {
    return of({ uploadUrl: 'https://s3.example/upload', mediaId: 'media-2' });
  }

  uploadMedia(): Observable<void> {
    return of(undefined);
  }
}

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

  lastCheckin: { gardenId: string; goalId: string; taskId: string; mediaId: string } | undefined;

  checkinTask(gardenId: string, goalId: string, taskId: string, mediaId: string): Observable<void> {
    this.lastCheckin = { gardenId, goalId, taskId, mediaId };
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
        { provide: GardenApi, useClass: FakeGardenApi },
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

  it('checking in a task uploads the photo, then calls GoalApi.checkinTask, then polls for feedback', () => {
    const file = new File(['x'], 'proof.jpg', { type: 'image/jpeg' });
    fixture.componentInstance.onTaskCheckin('t1', file);

    expect(fakeGoalApi.lastCheckin).toEqual({
      gardenId: 'g-1',
      goalId: 'goal-1',
      taskId: 't1',
      mediaId: 'media-2',
    });
    expect(fixture.componentInstance.checkingInTaskId()).toBeNull();
    expect(fixture.componentInstance.awaitingFeedbackForTaskId()).toBe('t1');
  });

  it('does not render "How this was decided" when the plan has no trace', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).not.toContain('How this was decided');
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
        { provide: GardenApi, useClass: FakeGardenApi },
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

describe('GoalDetailComponent (polling for check-in feedback)', () => {
  class CheckinPollingFakeGoalApi {
    callCount = 0;

    getGoalDetail(): Observable<GoalDetail> {
      this.callCount += 1;
      // Call 1 is the initial constructor fetch, call 2 is the poll's immediate first check
      // (still no feedback) — call 3+ (after one interval tick) simulates the orchestrator
      // having written feedback by then. Mirrors PollingFakeGoalApi's callCount convention above.
      const feedback = this.callCount >= 3 ? 'Looking good, keep it up!' : undefined;
      return of({
        ...DETAIL,
        tasks: [{ ...DETAIL.tasks[0], status: 'done', feedback }],
      });
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

    checkinTask(): Observable<void> {
      return of(undefined);
    }
  }

  it('shows the waiting indicator, then the feedback once it arrives — no manual refresh needed', fakeAsync(() => {
    TestBed.configureTestingModule({
      imports: [GoalDetailComponent],
      providers: [
        provideRouter([]),
        { provide: GoalApi, useClass: CheckinPollingFakeGoalApi },
        { provide: GardenApi, useClass: FakeGardenApi },
        { provide: CurrentGardenService, useValue: { gardenId: () => 'g-1' } },
        {
          provide: ActivatedRoute,
          useValue: { paramMap: of(convertToParamMap({ goalId: 'goal-1' })) },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GoalDetailComponent);
    fixture.detectChanges();

    const file = new File(['x'], 'proof.jpg', { type: 'image/jpeg' });
    fixture.componentInstance.onTaskCheckin('t1', file);

    expect(fixture.componentInstance.awaitingFeedbackForTaskId()).toBe('t1');
    expect(fixture.componentInstance.detail()?.tasks[0].feedback).toBeUndefined();

    tick(2000);

    expect(fixture.componentInstance.awaitingFeedbackForTaskId()).toBeNull();
    expect(fixture.componentInstance.detail()?.tasks[0].feedback).toBe(
      'Looking good, keep it up!',
    );
  }));
});

describe('GoalDetailComponent ("How this was decided")', () => {
  const DETAIL_WITH_TRACE: GoalDetail = {
    ...DETAIL,
    plan: {
      ...DETAIL.plan!,
      trace: [
        { agent: 'agronomy', says: 'Nitrogen is high.', ms: 2100 },
        { agent: 'orchestrator', says: 'Feed change recommended.', ms: 900, isOrchestrator: true },
      ],
    },
  };

  class TraceFakeGoalApi {
    getGoalDetail(): Observable<GoalDetail> {
      return of(DETAIL_WITH_TRACE);
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
    checkinTask(): Observable<void> {
      return of(undefined);
    }
  }

  let fixture: ComponentFixture<GoalDetailComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GoalDetailComponent],
      providers: [
        provideRouter([]),
        { provide: GoalApi, useClass: TraceFakeGoalApi },
        { provide: GardenApi, useClass: FakeGardenApi },
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

  it('renders the collapsed toggle with specialist count and total duration', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('How this was decided');
    expect(text).toContain('1 specialists · 3s · resolved at runtime');
    expect(fixture.nativeElement.querySelector('td-trace-entry')).toBeFalsy();
  });

  it('expands to show each trace entry when toggled', () => {
    fixture.componentInstance.toggleTrace();
    fixture.detectChanges();

    const entries = fixture.nativeElement.querySelectorAll('td-trace-entry');
    expect(entries.length).toBe(2);
  });
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
        { provide: GardenApi, useClass: FakeGardenApi },
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
