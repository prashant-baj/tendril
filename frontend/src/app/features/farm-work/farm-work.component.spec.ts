import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Observable, of } from 'rxjs';
import { FarmWorkComponent } from './farm-work.component';
import { FarmWorkApi } from '../../core/services/farm-work.service';
import { GardenApi } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import {
  Activity,
  ActivityLogRequest,
  ActivityRequest,
  GardenAction,
} from '../../core/models/farm-work.model';
import { Plant } from '../../core/models/plant.model';

const HUMIC: Activity = {
  activityId: 'a1',
  title: 'Humic acid drench',
  kind: 'input',
  dose: 'via drip',
  targets: 'all',
  schedule: { minDays: 21, maxDays: 28 },
  status: 'active',
  dueState: 'overdue',
  nextDueOn: '2026-11-13',
  overdueOn: '2026-11-20',
  source: 'user',
  assessment: { verdict: 'beneficial', summary: 'Good spacing.', benefits: ['roots'], risks: [], conflicts: [] },
};
const SPRAY: Activity = {
  ...HUMIC,
  activityId: 'a2',
  title: 'Copper spray',
  dueState: 'due',
  schedule: undefined,
  assessment: {
    verdict: 'harmful',
    summary: 'Kills the microbes from yesterday\'s jivamrut.',
    benefits: [],
    risks: ['microbe loss'],
    conflicts: [{ with: 'Jivamrut (23 Nov)', reason: '1 day apart' }],
    suggestion: 'Wait 10 days.',
  },
};
const SUGGESTION: GardenAction = {
  actionId: 's1',
  type: 'move_due',
  summary: "Move 'Foliar spray' to 2026-11-27?",
  reason: 'Rain tomorrow.',
  status: 'proposed',
  createdAt: 'x',
};
const PLANTS: Plant[] = [94, 95].map((n) => ({
  plantId: `p${n}`,
  name: `#${n} Coconut`,
  species: 'Coconut',
  variety: '',
  stage: 'young',
  icon: 'eco',
  healthState: 'healthy',
  meta: '',
  stakeNo: n,
}));

class FakeFarmWork {
  created: ActivityRequest[] = [];
  logged: ActivityLogRequest[] = [];
  updated: { id: string; req: ActivityRequest }[] = [];
  confirmed: string[] = [];
  rejected: string[] = [];
  listActivities(): Observable<Activity[]> {
    return of([HUMIC, SPRAY]);
  }
  listSuggestions(): Observable<GardenAction[]> {
    return of([SUGGESTION]);
  }
  listLogs() {
    return of({ logs: [], nextCursor: undefined });
  }
  createActivity(_g: string, req: ActivityRequest) {
    this.created.push(req);
    return of(HUMIC);
  }
  logActivity(_g: string, req: ActivityLogRequest) {
    this.logged.push(req);
    return of({});
  }
  updateActivity(_g: string, id: string, req: ActivityRequest) {
    this.updated.push({ id, req });
    return of(HUMIC);
  }
  confirmSuggestion(_g: string, id: string) {
    this.confirmed.push(id);
    return of(undefined);
  }
  rejectSuggestion(_g: string, id: string) {
    this.rejected.push(id);
    return of(undefined);
  }
}

class FakeGardenApi {
  getPlants() {
    return of(PLANTS);
  }
  requestMediaUpload(_g: string, req: { fileName: string }) {
    return of({ uploadUrl: 'https://s3/u', mediaId: `m-${req.fileName}` });
  }
  uploadMedia() {
    return of(undefined);
  }
}

describe('FarmWorkComponent', () => {
  let fixture: ComponentFixture<FarmWorkComponent>;
  let api: FakeFarmWork;

  beforeEach(async () => {
    api = new FakeFarmWork();
    await TestBed.configureTestingModule({
      imports: [FarmWorkComponent],
      providers: [
        { provide: FarmWorkApi, useValue: api },
        { provide: GardenApi, useClass: FakeGardenApi },
        { provide: CurrentGardenService, useValue: { gardenId: () => 'g-1' } },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(FarmWorkComponent);
    fixture.detectChanges();
  });

  it('shows planned work with its due state, schedule and verdict, and the suggestions', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Humic acid drench');
    expect(text).toContain('Overdue since 20 Nov 2026');
    expect(text).toContain('Every 21–28 days');
    expect(text).toContain('Beneficial');
    expect(text).toContain("Move 'Foliar spray' to 2026-11-27?");
    // A harmful verdict opens as a banner with its reasons.
    expect(text).toContain('Clashes with Jivamrut (23 Nov)');
  });

  it('plans recurring work with a 21-28 day window', () => {
    const c = fixture.componentInstance;
    c.startPlan();
    c.form.patchValue({ title: 'Humic acid drench', repeat: 'window', minDays: 21, maxDays: 28, date: '2026-11-25' });
    c.save();
    expect(api.created[0]).toEqual(
      jasmine.objectContaining({
        title: 'Humic acid drench',
        schedule: { minDays: 21, maxDays: 28 },
        startOn: '2026-11-25',
        targets: 'all',
      }),
    );
  });

  it('logs ad-hoc work on a few palms by number, with photos and a video', () => {
    const c = fixture.componentInstance;
    c.startLog();
    c.form.patchValue({ title: 'Mulched basins', kind: 'operation', targets: '94, 95' });
    const input = document.createElement('input');
    input.type = 'file';
    Object.defineProperty(input, 'files', {
      value: [
        new File(['x'], 'a.jpg', { type: 'image/jpeg' }),
        new File(['x'], 'b.mp4', { type: 'video/mp4' }),
      ],
    });
    c.onFiles({ target: input } as unknown as Event);
    c.save();
    expect(api.logged[0]).toEqual(
      jasmine.objectContaining({ title: 'Mulched basins', targets: ['p94', 'p95'], mediaIds: ['m-a.jpg', 'm-b.mp4'] }),
    );
  });

  it('refuses unknown plant numbers and oversized videos', () => {
    const c = fixture.componentInstance;
    expect(c.parseTargets('94, 7')).toBeNull();
    const input = document.createElement('input');
    input.type = 'file';
    const big = new File(['x'], 'big.mov', { type: 'video/quicktime' });
    Object.defineProperty(big, 'size', { value: 101 * 1024 * 1024 });
    Object.defineProperty(input, 'files', { value: [big] });
    c.onFiles({ target: input } as unknown as Event);
    expect(c.error()).toContain('100 MB');
    expect(c.files()).toEqual([]);
  });

  it('logging a planned activity carries its id', () => {
    const c = fixture.componentInstance;
    c.startLog(HUMIC);
    c.save();
    expect(api.logged[0]).toEqual(jasmine.objectContaining({ activityId: 'a1', title: 'Humic acid drench' }));
  });

  it('asks "Are you sure?" before logging work flagged harmful', () => {
    const c = fixture.componentInstance;
    const confirm = spyOn(window, 'confirm').and.returnValue(false);
    c.startLog(SPRAY);
    expect(confirm).toHaveBeenCalled();
    expect(c.mode()).toBe('none');
    confirm.and.returnValue(true);
    c.startLog(SPRAY);
    expect(c.mode()).toBe('log');
  });

  it('confirms or dismisses a suggestion, and pauses work', () => {
    const c = fixture.componentInstance;
    c.resolve(SUGGESTION, true);
    expect(api.confirmed).toEqual(['s1']);
    c.resolve({ ...SUGGESTION, actionId: 's2' }, false);
    expect(api.rejected).toEqual(['s2']);
    c.setStatus(HUMIC, 'paused');
    expect(api.updated[0]).toEqual({ id: 'a1', req: { status: 'paused' } });
  });
});
