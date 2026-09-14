import { Injectable } from '@angular/core';
import { generateId } from '../utils/id.util';

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
