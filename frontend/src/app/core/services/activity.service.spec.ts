import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { CLIENT_API_BASE_URL } from '../config/client-api.config';
import { HttpActivityApi, MockActivityApi } from './activity.service';

describe('MockActivityApi', () => {
  it('getActivity() returns an empty list (no garden created yet)', (done) => {
    new MockActivityApi().getActivity(null).subscribe((events) => {
      expect(events).toEqual([]);
      done();
    });
  });
});

describe('HttpActivityApi', () => {
  let api: HttpActivityApi;
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
    api = TestBed.inject(HttpActivityApi);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('getActivity(gardenId) GETs /gardens/{id}/activity', () => {
    let result: unknown;
    api.getActivity('g-1').subscribe((r) => (result = r));

    const req = httpMock.expectOne(`${baseUrl}/gardens/g-1/activity`);
    expect(req.request.method).toBe('GET');
    const body = [
      {
        type: 'goal.submitted',
        payload: { goalId: 'goal-1', description: 'help' },
        createdAt: '2026-01-01T00:00:00+00:00',
        goalId: 'goal-1',
      },
    ];
    req.flush(body);

    expect(result).toEqual(body);
  });

  it('getActivity(null) falls back to the mock (no garden created yet)', (done) => {
    api.getActivity(null).subscribe((events) => {
      expect(events).toEqual([]);
      done();
    });
  });
});
