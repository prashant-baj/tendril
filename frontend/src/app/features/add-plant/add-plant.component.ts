import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { Observable, of } from 'rxjs';
import { map, switchMap } from 'rxjs/operators';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { PhotoPickerComponent } from '../../shared/components/photo-picker/photo-picker.component';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import { GardenApi } from '../../core/services/garden.service';

/** OB-02 "Add a Plant (with a photo)": species (required), variety (optional), photo (optional). */
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

  readonly form = this.fb.nonNullable.group({
    species: ['', [Validators.required, Validators.minLength(1)]],
    variety: [''],
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

    const { species, variety } = this.form.getRawValue();
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
          }),
        ),
      )
      .subscribe({
        next: () => this.router.navigateByUrl('/garden'),
        error: () => {
          this.submitting.set(false);
          this.errorMessage.set(
            "Couldn't save your plant. Please check your connection and try again.",
          );
        },
      });
  }
}
