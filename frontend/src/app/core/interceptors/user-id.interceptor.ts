import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { UserIdentityService } from '../services/user-identity.service';

/** Attaches the per-browser `X-User-Id` header to every outgoing Client API request. */
export const userIdInterceptor: HttpInterceptorFn = (req, next) => {
  const userId = inject(UserIdentityService).getUserId();
  return next(req.clone({ setHeaders: { 'X-User-Id': userId } }));
};
