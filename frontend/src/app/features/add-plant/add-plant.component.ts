import { HttpErrorResponse } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { Observable, of } from 'rxjs';
import { map, switchMap } from 'rxjs/operators';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { PhotoPickerComponent } from '../../shared/components/photo-picker/photo-picker.component';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import { GardenApi } from '../../core/services/garden.service';

/** User-facing text for a failed create: a taken stake number (409), the API's own validation
 * message (400), or a generic connection message. */
export function addPlantErrorMessage(err: unknown, stakeNo?: number): string {
  if (err instanceof HttpErrorResponse) {
    if (err.status === 409) {
      return stakeNo !== undefined
        ? `Stake #${stakeNo} is already used by another plant in this garden.`
        : 'That stake number is already used by another plant in this garden.';
    }
    const message = (err.error as { message?: string } | null)?.message;
    if (err.status === 400 && message) return `Couldn't save: ${message}`;
  }
  return "Couldn't save your plant. Please check your connection and try again.";
}

/**
 * OB-02 "Add a Plant (with a photo)": species (required), variety, photo, and (ADR-0032) an
 * optional stake number — the number on the plant's physical stake, unique within the garden —
 * and an optional growth stage.
 */
@Component({
  selector: 'td-add-plant',
  standalone: true,
  imports: [ReactiveFormsModule, IconComponent, PhotoPickerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './add-plant.component.html',
  styleUrl: './add-plant.component.scss',
})
export class AddPlantComponent {
  private readonly fb = inject(FormBuilder);
  private readonly gardenApi = inject(GardenApi);
  private readonly currentGarden = inject(CurrentGardenService);
  private readonly router = inject(Router);

  readonly submitting = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly selectedFile = signal<File | null>(null);

  readonly form = this.fb.group({
    species: this.fb.nonNullable.control('', [Validators.required, Validators.minLength(1)]),
    variety: this.fb.nonNullable.control(''),
    stage: this.fb.nonNullable.control('', [Validators.maxLength(50)]),
    stakeNo: this.fb.control<number | null>(null, [
      Validators.min(1),
      Validators.max(99999),
      Validators.pattern(/^\d+$/),
    ]),
  });

  onFileSelected(file: File | null): void {
    this.selectedFile.set(file);
  }

  submit(): void {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }

    const gardenId = this.currentGarden.gardenId();
    if (!gardenId) {
      this.errorMessage.set('No garden found — set up your garden first.');
      return;
    }

    const { species, variety, stage, stakeNo } = this.form.getRawValue();
    const stake = stakeNo === null || `${stakeNo}` === '' ? undefined : Number(stakeNo);
    const file = this.selectedFile();
    this.submitting.set(true);
    this.errorMessage.set(null);

    const mediaId$: Observable<string | undefined> = file
      ? this.gardenApi
          .requestMediaUpload(gardenId, { contentType: file.type, fileName: file.name })
          .pipe(
            switchMap(({ uploadUrl, mediaId }) =>
              this.gardenApi.uploadMedia(uploadUrl, file).pipe(map(() => mediaId)),
            ),
          )
      : of(undefined);

    mediaId$
      .pipe(
        switchMap((mediaId) =>
          this.gardenApi.createPlant(gardenId, {
            species,
            variety: variety || undefined,
            mediaId,
            ...(stage.trim() ? { stage: stage.trim() } : {}),
            ...(stake !== undefined ? { stakeNo: stake } : {}),
          }),
        ),
      )
      .subscribe({
        next: () => this.router.navigateByUrl('/garden'),
        error: (err: unknown) => {
          this.submitting.set(false);
          this.errorMessage.set(addPlantErrorMessage(err, stake));
        },
      });
  }
}
