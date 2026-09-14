import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { UserIdentityService } from '../services/user-identity.service';
import { CLIENT_API_BASE_URL } from '../config/client-api.config';

/**
 * Attaches the per-browser `X-User-Id` header to Client API requests only. OB-02 introduced
 * requests that don't go to the Client API at all — a direct browser PUT to a presigned S3
 * URL (`HttpGardenApi.uploadMedia`) — and S3 would reject an extra unsigned header on a
 * presigned request with `SignatureDoesNotMatch`, so this must not touch non-Client-API URLs.
 */
export const userIdInterceptor: HttpInterceptorFn = (req, next) => {
  const baseUrl = inject(CLIENT_API_BASE_URL);
  if (!req.url.startsWith(baseUrl)) {
    return next(req);
  }
  const userId = inject(UserIdentityService).getUserId();
  return next(req.clone({ setHeaders: { 'X-User-Id': userId } }));
};
