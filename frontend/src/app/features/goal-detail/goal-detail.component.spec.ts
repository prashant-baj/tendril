import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { Observable, of } from 'rxjs';
import { GoalDetailComponent } from './goal-detail.component';
import { GoalApi, GoalDetail, MessageExtras } from '../../core/services/goal.service';
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

  lastExtras: MessageExtras | undefined;

  sendMessage(
    gardenId: string,
    goalId: string,
    content: string,
    extras?: MessageExtras,
  ): Observable<void> {
    this.lastSendMessage = { gardenId, goalId, content };
    this.lastExtras = extras;
    return of(undefined);
  }

  lastConfirm: string | undefined;
  lastReject: string | undefined;

  confirmAction(_g: string, _goal: string, actionId: string): Observable<void> {
    this.lastConfirm = actionId;
    return of(undefined);
  }

  rejectAction(_g: string, _goal: string, actionId: string): Observable<void> {
    this.lastReject = actionId;
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
    fixture.componentInstance.replyForm.patchValue({ content: 'Can we water less?' });
    fixture.componentInstance.sendMessage();

    expect(fakeGoalApi.lastSendMessage).toEqual({
      gardenId: 'g-1',
      goalId: 'goal-1',
      content: 'Can we water less?',
    });
  });

  it('does not send an empty reply', () => {
    fixture.componentInstance.replyForm.patchValue({ content: '' });
    fixture.componentInstance.sendMessage();

    expect(fakeGoalApi.lastSendMessage).toBeUndefined();
  });

  it('approving calls GoalApi.approve with the garden id and plan id', () => {
    fixture.componentInstance.approve();

    expect(fakeGoalApi.lastApprove).toEqual({ gardenId: 'g-1', planId: 'goal-1' });
  });

  it('checking in a task uploads the photo, then calls GoalApi.checkinTask, then waits for the reply', () => {
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

  it('sends a photo and a task with a chat message (ADR-0034)', () => {
    const c = fixture.componentInstance;
    const input = document.createElement('input');
    input.type = 'file';
    Object.defineProperty(input, 'files', { value: [new File(['x'], 'p.jpg', { type: 'image/jpeg' })] });
    c.onChatPhoto({ target: input } as unknown as Event);
    c.replyForm.patchValue({ content: '', taskId: 't1' });
    expect(c.canSend).toBeTrue();

    c.sendMessage();

    expect(fakeGoalApi.lastSendMessage?.content).toBe('');
    expect(fakeGoalApi.lastExtras).toEqual({ mediaIds: ['media-2'], taskId: 't1' });
    expect(c.chatPhoto()).toBeNull();
  });

  it('refuses a chat photo over 10 MB before uploading', () => {
    const c = fixture.componentInstance;
    const big = new File(['x'], 'big.jpg', { type: 'image/jpeg' });
    Object.defineProperty(big, 'size', { value: 11 * 1024 * 1024 });
    const input = document.createElement('input');
    input.type = 'file';
    Object.defineProperty(input, 'files', { value: [big] });
    c.onChatPhoto({ target: input } as unknown as Event);
    expect(c.chatPhoto()).toBeNull();
    expect(c.chatError()).toContain('up to 10 MB');
  });

  it('lists only pending tasks in the check-in picker', () => {
    expect(fixture.componentInstance.pendingTasks().map((t) => t.taskId)).toEqual(['t1']);
  });

  it('Confirm and Not now call the action endpoints, never a typed message', () => {
    const c = fixture.componentInstance;
    const action = {
      actionId: 'a1',
      type: 'set_plant' as const,
      summary: 'Is it #10?',
      status: 'proposed' as const,
      createdAt: 'x',
    };
    c.confirmAction(action);
    expect(fakeGoalApi.lastConfirm).toBe('a1');
    c.rejectAction({ ...action, actionId: 'a2' });
    expect(fakeGoalApi.lastReject).toBe('a2');
    expect(fakeGoalApi.lastSendMessage).toBeUndefined();
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

    fixture.componentInstance.replyForm.patchValue({ content: 'more info' });
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

describe('GoalDetailComponent (check-in is a chat turn, ADR-0034)', () => {
  class CheckinPollingFakeGoalApi {
    callCount = 0;

    getGoalDetail(): Observable<GoalDetail> {
      this.callCount += 1;
      // Call 1 = initial fetch, call 2 = the poll's immediate check (nothing yet); call 3+ (one
      // tick later) has the check-in message and Tendril's reply carrying a proposal.
      if (this.callCount < 3) {
        return of(DETAIL);
      }
      return of({
        ...DETAIL,
        messages: [
          ...DETAIL.messages,
          { role: 'user' as const, content: 'Check-in: Water deeply', createdAt: 'x1', taskId: 't1' },
          { role: 'assistant' as const, content: 'Soil looks moist now.', createdAt: 'x2', actionId: 'a1' },
        ],
        actions: [
          {
            actionId: 'a1',
            type: 'complete_task' as const,
            summary: "Mark 'Water deeply' done?",
            status: 'proposed' as const,
            createdAt: 'x2',
            taskId: 't1',
          },
        ],
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

  it('waits for the reply with a Confirm button; the task stays pending until confirmed', fakeAsync(() => {
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

    fixture.componentInstance.onTaskCheckin('t1', new File(['x'], 'proof.jpg', { type: 'image/jpeg' }));
    expect(fixture.componentInstance.awaitingFeedbackForTaskId()).toBe('t1');

    tick(2000);
    fixture.detectChanges();

    expect(fixture.componentInstance.awaitingFeedbackForTaskId()).toBeNull();
    expect(fixture.componentInstance.detail()?.tasks[0].status).toBe('pending');
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain("Mark 'Water deeply' done?");
    expect(text).toContain('Confirm');
    expect(text).toContain('Check-in: Water deeply');
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

describe('GoalDetailComponent (linked plant and repeated proposals)', () => {
  const ACTION = {
    actionId: 'a1',
    type: 'set_goal_plant' as const,
    summary: 'Link this goal to #13?',
    status: 'confirmed' as const,
    createdAt: 'x',
  };
  const LINKED: GoalDetail = {
    ...DETAIL,
    goal: { ...DETAIL.goal, plantId: 'p13', plantStakeNo: 13 },
    messages: [
      { role: 'assistant', content: 'first ask', createdAt: '1', actionId: 'a1' },
      { role: 'user', content: 'Plant # 13', createdAt: '2' },
      { role: 'assistant', content: 'asked again', createdAt: '3', actionId: 'a1' },
    ],
    actions: [ACTION],
  };
  let fixture: ComponentFixture<GoalDetailComponent>;

  beforeEach(async () => {
    const api = new FakeGoalApi();
    api.getGoalDetail = () => of(LINKED);
    await TestBed.configureTestingModule({
      imports: [GoalDetailComponent],
      providers: [
        provideRouter([]),
        { provide: GoalApi, useValue: api },
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

  it("shows the goal's plant number in the header", () => {
    const header: HTMLElement = fixture.nativeElement.querySelector('.header');
    expect(header.textContent).toContain('Plant #13');
  });

  it('shows a repeated proposal once, on the latest message that carries it', () => {
    const c = fixture.componentInstance;
    const [first, , latest] = LINKED.messages;
    expect(c.actionFor(first)).toBeUndefined();
    expect(c.actionFor(latest)?.actionId).toBe('a1');
    expect(fixture.nativeElement.querySelectorAll('td-action-proposal').length).toBe(1);
  });
});
