import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { Observable, of, throwError } from 'rxjs';
import { PlantDetailComponent } from './plant-detail.component';
import { GardenApi } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import {
  Plant,
  PlantDetail,
  PlantHistoryQuery,
  PlantLogEntry,
  PlantLogRequest,
} from '../../core/models/plant.model';

const PLANT: Plant = {
  plantId: 'p13',
  name: '#13 Coconut',
  species: 'Coconut',
  variety: 'Kalpa Samrudhi',
  stage: 'young',
  icon: 'eco',
  healthState: 'watch',
  meta: '',
  stakeNo: 13,
  latestStatus: 'watch',
  latestObservedOn: '2026-11-23',
};

const photo = (id: string) => ({ mediaId: id, thumbUrl: `https://s3/${id}-t`, url: `https://s3/${id}` });

const NEWEST: PlantLogEntry[] = [
  { entryId: 'e3', observedOn: '2026-11-23', kind: 'photo', source: 'confirmed', photos: [photo('m3')], goalId: 'goal-1' },
  { entryId: 'e2', observedOn: '2026-11-23', kind: 'log', source: 'user', status: 'watch', note: 'Dry soil', photos: [] },
  { entryId: 'e1', observedOn: '2026-08-02', kind: 'checkin', source: 'confirmed', note: 'Mulch', photos: [photo('m1')] },
];

class FakeGardenApi {
  queries: PlantHistoryQuery[] = [];
  logs: PlantLogRequest[] = [];
  stage: string | undefined;
  failWith: number | undefined;

  getPlant(_g: string, _p: string, query: PlantHistoryQuery = {}): Observable<PlantDetail> {
    this.queries.push(query);
    if (this.failWith) {
      return throwError(() => ({ status: this.failWith }));
    }
    if (query.order === 'oldest') {
      return of({ plant: PLANT, history: { entries: [...NEWEST].reverse() } });
    }
    if (query.cursor) {
      const older: PlantLogEntry = { entryId: 'e0', observedOn: '2026-07-01', kind: 'log', source: 'migration', photos: [] };
      return of({ plant: PLANT, history: { entries: [older] } });
    }
    return of({ plant: PLANT, history: { entries: NEWEST, nextCursor: 'c1' } });
  }

  addPlantLog(_g: string, _p: string, request: PlantLogRequest): Observable<PlantLogEntry> {
    this.logs.push(request);
    return of({ entryId: 'new', observedOn: '2026-11-23', kind: 'log', source: 'user', photos: [] });
  }

  updatePlantStage(_g: string, _p: string, stage: string): Observable<Plant> {
    this.stage = stage;
    return of({ ...PLANT, stage });
  }

  requestMediaUpload(): Observable<{ uploadUrl: string; mediaId: string }> {
    return of({ uploadUrl: 'https://s3/upload', mediaId: 'm9' });
  }

  uploadMedia(): Observable<void> {
    return of(undefined);
  }
}

function setup(api = new FakeGardenApi()): { fixture: ComponentFixture<PlantDetailComponent>; api: FakeGardenApi } {
  TestBed.configureTestingModule({
    imports: [PlantDetailComponent],
    providers: [
      provideRouter([]),
      { provide: GardenApi, useValue: api },
      { provide: CurrentGardenService, useValue: { gardenId: () => 'g-1' } },
      { provide: ActivatedRoute, useValue: { paramMap: of(convertToParamMap({ plantId: 'p13' })) } },
    ],
  });
  const fixture = TestBed.createComponent(PlantDetailComponent);
  fixture.detectChanges();
  return { fixture, api };
}

describe('PlantDetailComponent', () => {
  it('shows the plant, its latest status and the history grouped by date', () => {
    const { fixture } = setup();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('h1')?.textContent).toContain('#13 Coconut');
    expect(el.textContent).toContain('Last seen 23 Nov 2026');
    const days = fixture.componentInstance.days();
    expect(days.map((d) => d.label)).toEqual(['23 Nov 2026', '2 Aug 2026']);
    expect(days[0].entries.length).toBe(2);
    expect(el.textContent).toContain('Dry soil');
    expect(el.querySelectorAll('.day').length).toBe(2);
  });

  it('compares the first photo with the latest one', () => {
    const { fixture } = setup();
    const c = fixture.componentInstance;
    expect(c.firstPhoto()?.mediaId).toBe('m1');
    expect(c.latestPhoto()?.mediaId).toBe('m3');
    expect(c.showCompare()).toBeTrue();
  });

  it('loads older entries with the cursor', () => {
    const { fixture, api } = setup();
    fixture.componentInstance.loadMore();
    expect(api.queries.at(-1)).toEqual({ limit: 20, cursor: 'c1' });
    expect(fixture.componentInstance.entries().at(-1)?.entryId).toBe('e0');
    expect(fixture.componentInstance.nextCursor()).toBeUndefined();
  });

  it('saves a note with a status and date, then reloads', () => {
    const { fixture, api } = setup();
    const c = fixture.componentInstance;
    expect(c.canSave).toBeFalse();
    c.logForm.patchValue({ note: ' New frond ', status: 'healthy', observedOn: '2026-11-20' });
    const before = api.queries.length;
    c.saveLog();
    expect(api.logs).toEqual([{ note: 'New frond', status: 'healthy', observedOn: '2026-11-20' }]);
    expect(api.queries.length).toBeGreaterThan(before);
    expect(c.logForm.getRawValue().note).toBe('');
  });

  it('uploads a photo with the note', () => {
    const { fixture, api } = setup();
    const c = fixture.componentInstance;
    const input = document.createElement('input');
    input.type = 'file';
    Object.defineProperty(input, 'files', { value: [new File(['x'], 'p.jpg', { type: 'image/jpeg' })] });
    c.onLogPhoto({ target: input } as unknown as Event);
    c.saveLog();
    expect(api.logs[0]).toEqual({ mediaIds: ['m9'] });
  });

  it('marks a plant removed instead of deleting it', () => {
    const { fixture, api } = setup();
    spyOn(window, 'confirm').and.returnValue(true);
    fixture.componentInstance.markRemoved();
    expect(api.stage).toBe('removed');
    expect(fixture.componentInstance.plant()?.stage).toBe('removed');
  });

  it('"Check this plant" opens capture with the plant preselected', () => {
    const { fixture } = setup();
    const router = TestBed.inject(Router);
    const nav = spyOn(router, 'navigate');
    fixture.componentInstance.checkThisPlant();
    expect(nav).toHaveBeenCalledWith(['/capture'], { queryParams: { plantId: 'p13' } });
  });

  it('shows "not found" for a plant that is not in this garden', () => {
    const api = new FakeGardenApi();
    api.failWith = 404;
    const { fixture } = setup(api);
    expect(fixture.componentInstance.notFound()).toBeTrue();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Plant not found');
  });
});
