import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { CaptureComponent } from './capture.component';
import { GardenApi } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';

describe('CaptureComponent', () => {
  let fixture: ComponentFixture<CaptureComponent>;
  let component: CaptureComponent;
  let gardenApi: jasmine.SpyObj<GardenApi>;

  beforeEach(async () => {
    gardenApi = jasmine.createSpyObj<GardenApi>('GardenApi', [
      'createGoal',
      'requestMediaUpload',
      'uploadMedia',
      'getPlants',
    ]);
    gardenApi.getPlants.and.returnValue(of([]));

    await TestBed.configureTestingModule({
      imports: [CaptureComponent],
      providers: [
        { provide: GardenApi, useValue: gardenApi },
        { provide: CurrentGardenService, useValue: { gardenId: () => 'g-1' } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(CaptureComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('starts idle with the file-picker fallback shown (no photo required)', () => {
    expect(component.phase()).toBe('idle');
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Take or choose a photo');
  });

  it('does not submit with an empty description', () => {
    component.submit();
    expect(gardenApi.createGoal).not.toHaveBeenCalled();
    expect(component.phase()).toBe('idle');
  });

  it('submits without a photo and shows the submitted pending state (happy path)', () => {
    gardenApi.createGoal.and.returnValue(of({ goalId: 'goal-1', status: 'Intake' }));

    component.onDescriptionInput('leaves turning yellow');
    component.submit();

    expect(gardenApi.requestMediaUpload).not.toHaveBeenCalled();
    expect(gardenApi.createGoal).toHaveBeenCalledWith('g-1', {
      description: 'leaves turning yellow',
      mediaIds: undefined,
      plantId: undefined,
    });
    expect(component.phase()).toBe('submitted');
  });

  it('uploads the photo first, then submits with its mediaId (happy path with photo)', () => {
    gardenApi.requestMediaUpload.and.returnValue(
      of({ uploadUrl: 'https://s3.example/upload', mediaId: 'media-1' }),
    );
    gardenApi.uploadMedia.and.returnValue(of(undefined));
    gardenApi.createGoal.and.returnValue(of({ goalId: 'goal-1', status: 'Intake' }));

    const file = new File(['x'], 'tomato.jpg', { type: 'image/jpeg' });
    component.onFileSelected(file);
    component.onDescriptionInput('leaves turning yellow');
    component.submit();

    expect(gardenApi.requestMediaUpload).toHaveBeenCalledWith('g-1', {
      contentType: 'image/jpeg',
      fileName: 'tomato.jpg',
    });
    expect(gardenApi.uploadMedia).toHaveBeenCalledWith('https://s3.example/upload', file);
    expect(gardenApi.createGoal).toHaveBeenCalledWith('g-1', {
      description: 'leaves turning yellow',
      mediaIds: ['media-1'],
      plantId: undefined,
    });
    expect(component.phase()).toBe('submitted');
  });

  it('populates the plant picker and includes plantId when one is selected', () => {
    gardenApi.getPlants.and.returnValue(
      of([{ plantId: 'plant-1', name: 'Tomato #2' } as any]),
    );
    gardenApi.createGoal.and.returnValue(of({ goalId: 'goal-1', status: 'Intake' }));

    fixture = TestBed.createComponent(CaptureComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();

    expect(component.plants().length).toBe(1);

    component.onPlantSelected('plant-1');
    component.onDescriptionInput('leaves turning yellow');
    component.submit();

    expect(gardenApi.createGoal).toHaveBeenCalledWith('g-1', {
      description: 'leaves turning yellow',
      mediaIds: undefined,
      plantId: 'plant-1',
    });
  });

  it('shows a recoverable error when submission fails', () => {
    gardenApi.createGoal.and.returnValue(throwError(() => new Error('network down')));

    component.onDescriptionInput('leaves turning yellow');
    component.submit();

    expect(component.phase()).toBe('error');
    expect(component.errorMessage()).toBeTruthy();

    // recoverable: retrying with the same description can still succeed
    gardenApi.createGoal.and.returnValue(of({ goalId: 'goal-1', status: 'Intake' }));
    component.submit();
    expect(component.phase()).toBe('submitted');
  });
});
