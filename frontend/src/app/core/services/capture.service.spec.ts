import { TestBed } from '@angular/core/testing';
import { CaptureService } from './capture.service';

describe('CaptureService', () => {
  let service: CaptureService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.runInInjectionContext(() => new CaptureService());
  });

  afterEach(() => jasmine.clock().uninstall());

  it('starts idle', () => {
    expect(service.phase()).toBe('idle');
  });

  it('reveals one specialist every 900ms, then lands on "found" after the 5th', () => {
    jasmine.clock().install();
    service.runAnalysis();
    expect(service.phase()).toBe('analyzing');
    expect(service.liveSteps().filter((s) => s.state === 'done').length).toBe(0);

    jasmine.clock().tick(900);
    expect(service.liveSteps().filter((s) => s.state === 'done').length).toBe(1);
    expect(service.phase()).toBe('analyzing');

    jasmine.clock().tick(900 * 3); // steps 2-4
    expect(service.liveSteps().filter((s) => s.state === 'done').length).toBe(4);
    expect(service.phase()).toBe('analyzing');

    jasmine.clock().tick(900); // 5th and final step
    expect(service.phase()).toBe('found');
    expect(service.liveSteps().every((s) => s.state === 'done')).toBe(true);
  });

  it('reset() returns to idle and clears progress', () => {
    jasmine.clock().install();
    service.runAnalysis();
    jasmine.clock().tick(900);
    service.reset();
    expect(service.phase()).toBe('idle');
    // liveIndex is back to 0, so no specialist is marked "done" — the leading one shows
    // "active" until runAnalysis() is called again, but that's never rendered while idle.
    expect(service.liveSteps().some((s) => s.state === 'done')).toBe(false);
  });
});
