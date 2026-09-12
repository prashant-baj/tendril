import { TestBed } from '@angular/core/testing';
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { userIdInterceptor } from './user-id.interceptor';
import { UserIdentityService } from '../services/user-identity.service';

describe('userIdInterceptor', () => {
  let http: HttpClient;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([userIdInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpClient);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('attaches the X-User-Id header to every request', () => {
    const userId = TestBed.inject(UserIdentityService).getUserId();
    http.get('/gardens/g-1').subscribe();

    const req = httpMock.expectOne('/gardens/g-1');
    expect(req.request.headers.get('X-User-Id')).toBe(userId);
    req.flush({});
  });
});
