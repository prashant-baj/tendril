import { UserIdentityService } from './user-identity.service';

describe('UserIdentityService', () => {
  beforeEach(() => localStorage.removeItem('tendril:userId'));
  afterEach(() => localStorage.removeItem('tendril:userId'));

  it('generates and persists a userId on first use', () => {
    const service = new UserIdentityService();
    const id = service.getUserId();
    expect(id).toBeTruthy();
    expect(localStorage.getItem('tendril:userId')).toBe(id);
  });

  it('reuses the persisted userId across instances', () => {
    const first = new UserIdentityService().getUserId();
    const second = new UserIdentityService().getUserId();
    expect(second).toBe(first);
  });

  it('falls back when crypto.randomUUID is unavailable (insecure context, ADR-0010)', () => {
    // crypto.randomUUID() only exists in a secure context (HTTPS/localhost); this deployment
    // is plain HTTP, where it's undefined — this reproduces that and must not throw.
    const original = crypto.randomUUID;
    (crypto as { randomUUID?: unknown }).randomUUID = undefined;
    try {
      const id = new UserIdentityService().getUserId();
      expect(id).toBeTruthy();
      expect(id).toMatch(/^[0-9a-f-]{36}$/);
    } finally {
      crypto.randomUUID = original;
    }
  });
});
