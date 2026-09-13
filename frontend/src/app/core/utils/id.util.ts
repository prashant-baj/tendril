/**
 * `crypto.randomUUID()` only exists in a secure context (HTTPS/localhost) — this app is
 * currently deployed over plain HTTP (ADR-0010), where it's simply undefined, so calling it
 * throws a TypeError. Originally written for `UserIdentityService`'s anonymous per-browser id;
 * reused wherever else a client-generated id is needed (e.g. a garden photo uploaded before the
 * garden itself exists) — none of these are security tokens, so a non-cryptographic fallback is
 * fine everywhere it's used.
 */
export function generateId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
