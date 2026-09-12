import { Injectable } from '@angular/core';

const STORAGE_KEY = 'tendril:userId';

/**
 * Per-browser anonymous identity (ADR-0004's auth seam): generated once and persisted in
 * localStorage until Cognito auth lands. Every Client API request carries this as the
 * `X-User-Id` header (see `userIdInterceptor`).
 */
@Injectable({ providedIn: 'root' })
export class UserIdentityService {
  private readonly userId: string;

  constructor() {
    const existing = localStorage.getItem(STORAGE_KEY);
    if (existing) {
      this.userId = existing;
    } else {
      this.userId = generateId();
      localStorage.setItem(STORAGE_KEY, this.userId);
    }
  }

  getUserId(): string {
    return this.userId;
  }
}

/**
 * `crypto.randomUUID()` only exists in a secure context (HTTPS/localhost) — this app is
 * currently deployed over plain HTTP (ADR-0010), where it's simply undefined, so calling it
 * throws a TypeError. That throw happens synchronously inside `userIdInterceptor` on the
 * first real HTTP request, before any network call is made — surfacing as a silent request
 * failure with nothing in the Network tab. This is just an anonymous per-browser label (not a
 * security token), so a non-cryptographic fallback is fine.
 */
function generateId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
