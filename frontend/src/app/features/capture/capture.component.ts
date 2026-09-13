import { ChangeDetectionStrategy, Component, inject, OnInit, signal } from '@angular/core';
import { Observable, of } from 'rxjs';
import { map, switchMap } from 'rxjs/operators';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { PhotoPickerComponent } from '../../shared/components/photo-picker/photo-picker.component';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import { GardenApi } from '../../core/services/garden.service';
import { Plant } from '../../core/models/plant.model';

type CapturePhase = 'idle' | 'submitting' | 'submitted' | 'error';

/**
 * WS-05: a real entry point into the pipeline — replaces the scripted `CaptureService` timer
 * (idle → analyzing → fabricated findings) with an actual `createGoal` call. There's no
 * GET-goal/status endpoint yet (WS-04's "carried forward": live push of the orchestrator's
 * result is a separate future story), so "submitted" is a plain pending state, not a redirect
 * to a goal-detail screen that would otherwise show `MockGoalApi`'s unrelated fixture data for
 * a real goal id.
 */
@Component({
  selector: 'td-capture',
  standalone: true,
  imports: [IconComponent, PhotoPickerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './capture.component.html',
  styleUrl: './capture.component.scss',
})
export class CaptureComponent implements OnInit {
  private readonly gardenApi = inject(GardenApi);
  private readonly currentGarden = inject(CurrentGardenService);

  readonly phase = signal<CapturePhase>('idle');
  readonly errorMessage = signal<string | null>(null);
  readonly description = signal('');
  readonly selectedFile = signal<File | null>(null);
  readonly plants = signal<Plant[]>([]);
  /** Empty string means "whole garden / not sure" — no plantId sent. */
  readonly selectedPlantId = signal<string>('');

  ngOnInit(): void {
    const gardenId = this.currentGarden.gardenId();
    this.gardenApi.getPlants(gardenId).subscribe((plants) => this.plants.set(plants));
  }

  onFileSelected(file: File | null): void {
    this.selectedFile.set(file);
  }

  onDescriptionInput(value: string): void {
    this.description.set(value);
  }

  onPlantSelected(plantId: string): void {
    this.selectedPlantId.set(plantId);
  }

  submit(): void {
    const description = this.description().trim();
    if (!description || this.phase() === 'submitting') {
      return;
    }

    const gardenId = this.currentGarden.gardenId();
    if (!gardenId) {
      this.phase.set('error');
      this.errorMessage.set('No garden found — set up your garden first.');
      return;
    }

    this.phase.set('submitting');
    this.errorMessage.set(null);

    const file = this.selectedFile();
    const mediaIds$: Observable<string[] | undefined> = file
      ? this.gardenApi
          .requestMediaUpload(gardenId, { contentType: file.type, fileName: file.name })
          .pipe(
            switchMap(({ uploadUrl, mediaId }) =>
              this.gardenApi.uploadMedia(uploadUrl, file).pipe(map(() => [mediaId])),
            ),
          )
      : of(undefined);

    const plantId = this.selectedPlantId() || undefined;

    mediaIds$
      .pipe(
        switchMap((mediaIds) =>
          this.gardenApi.createGoal(gardenId, { description, mediaIds, plantId }),
        ),
      )
      .subscribe({
        next: () => this.phase.set('submitted'),
        error: () => {
          this.phase.set('error');
          this.errorMessage.set(
            "Couldn't submit your issue. Please check your connection and try again.",
          );
        },
      });
  }

  retake(): void {
    this.phase.set('idle');
    this.errorMessage.set(null);
    this.description.set('');
    this.selectedFile.set(null);
    this.selectedPlantId.set('');
  }
}
