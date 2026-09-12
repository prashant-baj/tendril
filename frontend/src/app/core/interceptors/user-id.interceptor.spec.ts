import { TestBed } from '@angular/core/testing';
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { userIdInterceptor } from './user-id.interceptor';
import { UserIdentityService } from '../services/user-identity.service';
import { CLIENT_API_BASE_URL } from '../config/client-api.config';

describe('userIdInterceptor', () => {
  let http: HttpClient;
  let httpMock: HttpTestingController;
  const baseUrl = 'https://api.example.test';

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([userIdInterceptor])),
        provideHttpClientTesting(),
        { provide: CLIENT_API_BASE_URL, useValue: baseUrl },
      ],
    });
    http = TestBed.inject(HttpClient);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('attaches the X-User-Id header to Client API requests', () => {
    const userId = TestBed.inject(UserIdentityService).getUserId();
    http.get(`${baseUrl}/gardens/g-1`).subscribe();

    const req = httpMock.expectOne(`${baseUrl}/gardens/g-1`);
    expect(req.request.headers.get('X-User-Id')).toBe(userId);
    req.flush({});
  });

  it('does not attach X-User-Id to requests outside the Client API (e.g. a presigned S3 upload)', () => {
    // A real presigned PUT would reject an extra unsigned header with SignatureDoesNotMatch
    // (OB-02) — this must stay untouched.
    http.put('https://some-bucket.s3.amazonaws.com/key?presigned=1', new Blob()).subscribe();

    const req = httpMock.expectOne('https://some-bucket.s3.amazonaws.com/key?presigned=1');
    expect(req.request.headers.has('X-User-Id')).toBe(false);
    req.flush({});
  });
});
