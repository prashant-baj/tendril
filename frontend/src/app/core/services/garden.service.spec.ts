import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { CLIENT_API_BASE_URL } from '../config/client-api.config';
import { HttpGardenApi, MockGardenApi } from './garden.service';

describe('MockGardenApi', () => {
  let api: MockGardenApi;

  beforeEach(() => {
    api = new MockGardenApi();
  });

  it('createGarden() then getGardenById() round-trips the given fields', (done) => {
    api.createGarden({ name: 'New Garden', geolocation: 'Mumbai', vision: 'herbs' }).subscribe(({ gardenId }) => {
      expect(gardenId).toBeTruthy();
      api.getGardenById(gardenId).subscribe((garden) => {
        expect(garden).toEqual({
          gardenId,
          name: 'New Garden',
          vision: 'herbs',
          geolocation: 'Mumbai',
          climateZone: '',
        });
        done();
      });
    });
  });

  it('getGardenById() falls back to the default fixture for an unknown id', (done) => {
    api.getGardenById('nonexistent').subscribe((garden) => {
      expect(garden.gardenId).toBe('balcony-kitchen-garden');
      done();
    });
  });
});

describe('HttpGardenApi', () => {
  let api: HttpGardenApi;
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
    api = TestBed.inject(HttpGardenApi);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('createGarden() POSTs to /gardens and returns the created id', () => {
    let result: { gardenId: string } | undefined;
    api.createGarden({ name: 'G', geolocation: 'Pune' }).subscribe((r) => (result = r));

    const req = httpMock.expectOne(`${baseUrl}/gardens`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ name: 'G', geolocation: 'Pune' });
    req.flush({ gardenId: 'g-1' });

    expect(result).toEqual({ gardenId: 'g-1' });
  });

  it('getGardenById() GETs /gardens/{id} and maps the response', () => {
    let result: unknown;
    api.getGardenById('g-1').subscribe((r) => (result = r));

    const req = httpMock.expectOne(`${baseUrl}/gardens/g-1`);
    expect(req.request.method).toBe('GET');
    req.flush({
      gardenId: 'g-1',
      name: 'G',
      geolocation: 'Pune',
      vision: 'veggies',
      ownerUserId: 'u-1',
      createdAt: '2026-09-12T00:00:00+00:00',
    });

    expect(result).toEqual({
      gardenId: 'g-1',
      name: 'G',
      geolocation: 'Pune',
      vision: 'veggies',
      climateZone: '',
    });
  });

  it('getGarden()/getPlants() still delegate to the mock (not backed by a real endpoint yet)', (done) => {
    api.getGarden().subscribe((garden) => {
      expect(garden.gardenId).toBe('balcony-kitchen-garden');
      done();
    });
  });
});
