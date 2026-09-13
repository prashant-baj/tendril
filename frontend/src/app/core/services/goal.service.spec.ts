import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { CLIENT_API_BASE_URL } from '../config/client-api.config';
import { HttpGoalApi, MockGoalApi } from './goal.service';

describe('MockGoalApi', () => {
  let api: MockGoalApi;

  beforeEach(() => {
    api = new MockGoalApi();
  });

  it('getGoals() returns an empty list (no goal backend before a garden exists)', (done) => {
    api.getGoals(null).subscribe((goals) => {
      expect(goals).toEqual([]);
      done();
    });
  });

  it('getGoalDetail() returns undefined', (done) => {
    api.getGoalDetail(null, 'goal-1').subscribe((detail) => {
      expect(detail).toBeUndefined();
      done();
    });
  });
});

describe('HttpGoalApi', () => {
  let api: HttpGoalApi;
  let httpMock: HttpTestingController;
  const baseUrl = 'https://api.example.test';

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: CLIENT_API_BASE_URL, useValue: baseUrl },
      ],
    });
    api = TestBed.inject(HttpGoalApi);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('getGoals(gardenId) GETs /gardens/{id}/goals', () => {
    let result: unknown;
    api.getGoals('g-1').subscribe((r) => (result = r));

    const req = httpMock.expectOne(`${baseUrl}/gardens/g-1/goals`);
    expect(req.request.method).toBe('GET');
    req.flush([{ goalId: 'goal-1', description: 'help', type: 'diagnosis', status: 'Intake' }]);

    expect(result).toEqual([
      { goalId: 'goal-1', description: 'help', type: 'diagnosis', status: 'Intake' },
    ]);
  });

  it('getGoals(null) falls back to the mock (no garden created yet)', (done) => {
    api.getGoals(null).subscribe((goals) => {
      expect(goals).toEqual([]);
      done();
    });
  });

  it('getGoalDetail(gardenId, goalId) GETs /gardens/{id}/goals/{goalId}', () => {
    let result: unknown;
    api.getGoalDetail('g-1', 'goal-1').subscribe((r) => (result = r));

    const req = httpMock.expectOne(`${baseUrl}/gardens/g-1/goals/goal-1`);
    expect(req.request.method).toBe('GET');
    const body = {
      goal: { goalId: 'goal-1', description: 'help', type: 'diagnosis', status: 'PlanProposed' },
      tasks: [],
      media: [],
      messages: [],
    };
    req.flush(body);

    expect(result).toEqual(body);
  });

  it('getGoalDetail(null, goalId) falls back to the mock', (done) => {
    api.getGoalDetail(null, 'goal-1').subscribe((detail) => {
      expect(detail).toBeUndefined();
      done();
    });
  });

  it('sendMessage() POSTs to /gardens/{id}/goals/{goalId}/messages', () => {
    let completed = false;
    api.sendMessage('g-1', 'goal-1', 'water less?').subscribe(() => (completed = true));

    const req = httpMock.expectOne(`${baseUrl}/gardens/g-1/goals/goal-1/messages`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ content: 'water less?' });
    req.flush(null);

    expect(completed).toBe(true);
  });

  it('approve() POSTs to /gardens/{id}/plans/{planId}/approve', () => {
    let completed = false;
    api.approve('g-1', 'goal-1').subscribe(() => (completed = true));

    const req = httpMock.expectOne(`${baseUrl}/gardens/g-1/plans/goal-1/approve`);
    expect(req.request.method).toBe('POST');
    req.flush(null);

    expect(completed).toBe(true);
  });

  it('checkinTask() POSTs to /gardens/{id}/goals/{goalId}/tasks/{taskId}/checkins', () => {
    let completed = false;
    api.checkinTask('g-1', 'goal-1', 'task-1', 'media-1').subscribe(() => (completed = true));

    const req = httpMock.expectOne(
      `${baseUrl}/gardens/g-1/goals/goal-1/tasks/task-1/checkins`,
    );
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ mediaId: 'media-1' });
    req.flush(null);

    expect(completed).toBe(true);
  });
});
