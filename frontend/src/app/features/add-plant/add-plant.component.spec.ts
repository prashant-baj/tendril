import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { AddPlantComponent } from './add-plant.component';
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

    component.form.setValue({ species: 'Tomato', variety: 'Pusa Ruby' });
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
    component.form.setValue({ species: 'Tomato', variety: '' });
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
    component.form.setValue({ species: 'Tomato', variety: '' });
    component.submit();

    expect(gardenApi.createPlant).not.toHaveBeenCalled();
    expect(component.errorMessage()).toBeTruthy();
    expect(component.submitting()).toBe(false);
    expect(navigateSpy).not.toHaveBeenCalled();
  });
});
