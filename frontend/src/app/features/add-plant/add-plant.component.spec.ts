import { HttpErrorResponse } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { AddPlantComponent, addPlantErrorMessage } from './add-plant.component';
import { GardenApi } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';

describe('AddPlantComponent', () => {
  let fixture: ComponentFixture<AddPlantComponent>;
  let component: AddPlantComponent;
  let gardenApi: jasmine.SpyObj<GardenApi>;
  let router: Router;

  beforeEach(async () => {
    gardenApi = jasmine.createSpyObj<GardenApi>('GardenApi', [
      'createPlant',
      'requestMediaUpload',
      'uploadMedia',
    ]);

    await TestBed.configureTestingModule({
      imports: [AddPlantComponent],
      providers: [
        provideRouter([]),
        { provide: GardenApi, useValue: gardenApi },
        { provide: CurrentGardenService, useValue: { gardenId: () => 'g-1' } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(AddPlantComponent);
    component = fixture.componentInstance;
    router = TestBed.inject(Router);
    fixture.detectChanges();
  });

  it('starts with the file-picker fallback (no photo selected yet)', () => {
    expect(component.selectedFile()).toBeNull();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Take or choose a photo');
  });

  it('does not submit when species is missing', () => {
    component.submit();
    expect(gardenApi.createPlant).not.toHaveBeenCalled();
    expect(component.form.controls.species.touched).toBe(true);
  });

  it('creates the plant (no photo) and navigates to /garden on success', () => {
    gardenApi.createPlant.and.returnValue(of({ plantId: 'p-1' }));
    const navigateSpy = spyOn(router, 'navigateByUrl');

    component.form.patchValue({ species: 'Tomato', variety: 'Pusa Ruby' });
    component.submit();

    expect(gardenApi.requestMediaUpload).not.toHaveBeenCalled();
    expect(gardenApi.createPlant).toHaveBeenCalledWith('g-1', {
      species: 'Tomato',
      variety: 'Pusa Ruby',
      mediaId: undefined,
    });
    expect(navigateSpy).toHaveBeenCalledWith('/garden');
  });

  it('uploads the photo first, then creates the plant with its mediaId (happy path)', () => {
    gardenApi.requestMediaUpload.and.returnValue(
      of({ uploadUrl: 'https://s3.example/upload', mediaId: 'media-1' }),
    );
    gardenApi.uploadMedia.and.returnValue(of(undefined));
    gardenApi.createPlant.and.returnValue(of({ plantId: 'p-1' }));
    const navigateSpy = spyOn(router, 'navigateByUrl');

    const file = new File(['x'], 'tomato.jpg', { type: 'image/jpeg' });
    component.onFileSelected(file);
    component.form.patchValue({ species: 'Tomato', variety: '' });
    component.submit();

    expect(gardenApi.requestMediaUpload).toHaveBeenCalledWith('g-1', {
      contentType: 'image/jpeg',
      fileName: 'tomato.jpg',
    });
    expect(gardenApi.uploadMedia).toHaveBeenCalledWith('https://s3.example/upload', file);
    expect(gardenApi.createPlant).toHaveBeenCalledWith('g-1', {
      species: 'Tomato',
      variety: undefined,
      mediaId: 'media-1',
    });
    expect(navigateSpy).toHaveBeenCalledWith('/garden');
  });

  it('shows a recoverable error when the S3 upload fails', () => {
    gardenApi.requestMediaUpload.and.returnValue(
      of({ uploadUrl: 'https://s3.example/upload', mediaId: 'media-1' }),
    );
    gardenApi.uploadMedia.and.returnValue(throwError(() => new Error('upload failed')));
    const navigateSpy = spyOn(router, 'navigateByUrl');

    component.onFileSelected(new File(['x'], 'tomato.jpg', { type: 'image/jpeg' }));
    component.form.patchValue({ species: 'Tomato', variety: '' });
    component.submit();

    expect(gardenApi.createPlant).not.toHaveBeenCalled();
    expect(component.errorMessage()).toBeTruthy();
    expect(component.submitting()).toBe(false);
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  // --- Stake number + stage (ADR-0032) ---------------------------------------------------------

  it('sends the stake number and stage when given', () => {
    gardenApi.createPlant.and.returnValue(of({ plantId: 'p-74', stakeNo: 74 }));
    spyOn(router, 'navigateByUrl');

    component.form.patchValue({ species: 'Coconut', stakeNo: 74, stage: ' young ' });
    component.submit();

    expect(gardenApi.createPlant).toHaveBeenCalledWith('g-1', {
      species: 'Coconut',
      variety: undefined,
      mediaId: undefined,
      stage: 'young',
      stakeNo: 74,
    });
  });

  it('explains a taken stake number (409) and stays on the page', () => {
    gardenApi.createPlant.and.returnValue(
      throwError(() => new HttpErrorResponse({ status: 409, error: { message: 'dup' } })),
    );
    const navigateSpy = spyOn(router, 'navigateByUrl');

    component.form.patchValue({ species: 'Coconut', stakeNo: 74 });
    component.submit();

    expect(component.errorMessage()).toBe('Stake #74 is already used by another plant in this garden.');
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('rejects an invalid stake number before calling the API', () => {
    component.form.patchValue({ species: 'Coconut', stakeNo: 0 });
    component.submit();
    expect(gardenApi.createPlant).not.toHaveBeenCalled();
  });

  it('shows the API validation message on a 400', () => {
    expect(
      addPlantErrorMessage(
        new HttpErrorResponse({ status: 400, error: { message: 'stage must be a non-empty string' } }),
      ),
    ).toBe("Couldn't save: stage must be a non-empty string");
    expect(addPlantErrorMessage(new Error('offline'))).toContain('check your connection');
  });
});
