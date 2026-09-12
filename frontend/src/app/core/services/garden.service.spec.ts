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

  it('getGarden() still delegates to the mock (not backed by a real endpoint yet)', (done) => {
    api.getGarden().subscribe((garden) => {
      expect(garden.gardenId).toBe('balcony-kitchen-garden');
      done();
    });
  });

  it('requestMediaUpload() POSTs to /gardens/{id}/media', () => {
    let result: unknown;
    api
      .requestMediaUpload('g-1', { contentType: 'image/jpeg', fileName: 'tomato.jpg' })
      .subscribe((r) => (result = r));

    const req = httpMock.expectOne(`${baseUrl}/gardens/g-1/media`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ contentType: 'image/jpeg', fileName: 'tomato.jpg' });
    req.flush({ uploadUrl: 'https://s3.example/upload', mediaId: 'media-1' });

    expect(result).toEqual({ uploadUrl: 'https://s3.example/upload', mediaId: 'media-1' });
  });

  it('uploadMedia() PUTs the file directly to the presigned URL with its content-type', () => {
    const file = new File(['x'], 'tomato.jpg', { type: 'image/jpeg' });
    let completed = false;
    api.uploadMedia('https://s3.example/upload', file).subscribe(() => (completed = true));

    const req = httpMock.expectOne('https://s3.example/upload');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toBe(file);
    expect(req.request.headers.get('Content-Type')).toBe('image/jpeg');
    req.flush(null);

    expect(completed).toBe(true);
  });

  it('createPlant() POSTs to /gardens/{id}/plants and prepends the result onto getPlants()', (done) => {
    let result: { plantId: string } | undefined;
    api.createPlant('g-1', { species: 'Tomato', variety: 'Pusa Ruby' }).subscribe((r) => {
      result = r;
    });

    const req = httpMock.expectOne(`${baseUrl}/gardens/g-1/plants`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ species: 'Tomato', variety: 'Pusa Ruby' });
    req.flush({ plantId: 'p-1' });

    expect(result).toEqual({ plantId: 'p-1' });

    api.getPlants().subscribe((plants) => {
      expect(plants[0]).toEqual(
        jasmine.objectContaining({ plantId: 'p-1', species: 'Tomato', variety: 'Pusa Ruby' }),
      );
      done();
    });
  });

  it('createGoal() POSTs to /gardens/{id}/goals', () => {
    let result: { goalId: string; status: string } | undefined;
    api
      .createGoal('g-1', { description: 'leaves turning yellow', mediaIds: ['media-1'] })
      .subscribe((r) => (result = r));

    const req = httpMock.expectOne(`${baseUrl}/gardens/g-1/goals`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({
      description: 'leaves turning yellow',
      mediaIds: ['media-1'],
    });
    req.flush({ goalId: 'goal-1', status: 'Intake' });

    expect(result).toEqual({ goalId: 'goal-1', status: 'Intake' });
  });
});
