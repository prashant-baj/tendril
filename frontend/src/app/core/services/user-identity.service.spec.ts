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
});
