import { HttpErrorResponse } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { BulkAddPlantsComponent } from './bulk-add-plants.component';
import { GardenApi } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import { Garden } from '../../core/models/garden.model';

const GARDEN: Garden = {
  gardenId: 'g-1',
  name: 'Farm',
  geolocation: 'Kopargaon',
  vision: '',
  climateZone: '',
  context: { stakeRange: { min: 1, max: 105 } },
};

describe('BulkAddPlantsComponent', () => {
  let fixture: ComponentFixture<BulkAddPlantsComponent>;
  let component: BulkAddPlantsComponent;
  let gardenApi: jasmine.SpyObj<GardenApi>;
  let router: Router;

  beforeEach(async () => {
    gardenApi = jasmine.createSpyObj<GardenApi>('GardenApi', ['getGardenById', 'bulkCreatePlants']);
    gardenApi.getGardenById.and.returnValue(of(GARDEN));
    await TestBed.configureTestingModule({
      imports: [BulkAddPlantsComponent],
      providers: [
        provideRouter([]),
        { provide: GardenApi, useValue: gardenApi },
        { provide: CurrentGardenService, useValue: { gardenId: () => 'g-1' } },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(BulkAddPlantsComponent);
    component = fixture.componentInstance;
    router = TestBed.inject(Router);
    fixture.detectChanges();
  });

  it("pre-fills the range from the garden's stake range", () => {
    expect(component.form.controls.stakeFrom.value).toBe(1);
    expect(component.form.controls.stakeTo.value).toBe(105);
    expect(component.count).toBe(105);
  });

  it('creates the range and shows created/skipped counts', () => {
    gardenApi.bulkCreatePlants.and.returnValue(
      of({
        created: [
          { plantId: 'p1', stakeNo: 1 },
          { plantId: 'p3', stakeNo: 3, outOfRange: true },
        ],
        skipped: [2],
      }),
    );
    component.form.patchValue({ species: ' Coconut ', variety: 'Kalpa Samrudhi', stakeFrom: 1, stakeTo: 3 });
    component.submit();

    expect(gardenApi.bulkCreatePlants).toHaveBeenCalledWith('g-1', {
      species: 'Coconut',
      variety: 'Kalpa Samrudhi',
      stakeFrom: 1,
      stakeTo: 3,
    });
    expect(component.result()?.created.length).toBe(2);
    expect(component.outOfRangeCount).toBe(1);
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('2 plants added');
    expect(text).toContain('1 skipped');
  });

  it('rejects a reversed range and more than 200 at once', () => {
    component.form.patchValue({ species: 'Coconut', stakeFrom: 10, stakeTo: 5 });
    expect(component.form.errors?.['order']).toBeTrue();
    component.form.patchValue({ stakeFrom: 1, stakeTo: 201 });
    expect(component.form.errors?.['tooMany']).toBeTrue();
    component.submit();
    expect(gardenApi.bulkCreatePlants).not.toHaveBeenCalled();
  });

  it('explains a mid-run failure and keeps the form for a retry', () => {
    gardenApi.bulkCreatePlants.and.returnValue(
      throwError(() => new HttpErrorResponse({ status: 500, error: { message: 'could not create plant #40; retry to resume' } })),
    );
    component.form.patchValue({ species: 'Coconut' });
    component.submit();
    expect(component.errorMessage()).toContain('retry to resume');
    expect(component.result()).toBeNull();
    expect(component.submitting()).toBe(false);
  });

  it('goes to the garden from the result screen', () => {
    const navigateSpy = spyOn(router, 'navigateByUrl');
    component.goToGarden();
    expect(navigateSpy).toHaveBeenCalledWith('/garden');
  });
});
