import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { CLIENT_API_BASE_URL } from '../config/client-api.config';
import { HttpTaskApi, MockTaskApi } from './task.service';

describe('MockTaskApi', () => {
  it('getTasks() returns an empty list (no garden created yet)', (done) => {
    new MockTaskApi().getTasks(null).subscribe((tasks) => {
      expect(tasks).toEqual([]);
      done();
    });
  });
});

describe('HttpTaskApi', () => {
  let api: HttpTaskApi;
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
    api = TestBed.inject(HttpTaskApi);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('getTasks(gardenId) GETs /gardens/{id}/tasks', () => {
    let result: unknown;
    api.getTasks('g-1').subscribe((r) => (result = r));

    const req = httpMock.expectOne(`${baseUrl}/gardens/g-1/tasks`);
    expect(req.request.method).toBe('GET');
    const body = [
      { taskId: 't1', goalId: 'goal-1', title: 'Water', detail: 'Deeply', scope: 'plant', status: 'pending' },
    ];
    req.flush(body);

    expect(result).toEqual(body);
  });

  it('getTasks(null) falls back to the mock (no garden created yet)', (done) => {
    api.getTasks(null).subscribe((tasks) => {
      expect(tasks).toEqual([]);
      done();
    });
  });
});
