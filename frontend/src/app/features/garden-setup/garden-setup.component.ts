import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { Observable, of } from 'rxjs';
import { map, switchMap } from 'rxjs/operators';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { PhotoPickerComponent } from '../../shared/components/photo-picker/photo-picker.component';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import { GardenApi } from '../../core/services/garden.service';
import { generateId } from '../../core/utils/id.util';

/** OB-01 "Setup My Garden": name / geolocation (required), vision + photo (optional). */
@Component({
  selector: 'td-garden-setup',
  standalone: true,
  imports: [ReactiveFormsModule, IconComponent, PhotoPickerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './garden-setup.component.html',
  styleUrl: './garden-setup.component.scss',
})
export class GardenSetupComponent {
  private readonly fb = inject(FormBuilder);
  private readonly gardenApi = inject(GardenApi);
  private readonly currentGarden = inject(CurrentGardenService);
  private readonly router = inject(Router);

  readonly submitting = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly selectedFile = signal<File | null>(null);

  readonly form = this.fb.nonNullable.group({
    name: ['', [Validators.required, Validators.minLength(1)]],
    geolocation: ['', [Validators.required, Validators.minLength(1)]],
    vision: [''],
  });

  onFileSelected(file: File | null): void {
    this.selectedFile.set(file);
  }

  submit(): void {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }

    const { name, geolocation, vision } = this.form.getRawValue();
    const file = this.selectedFile();
    this.submitting.set(true);
    this.errorMessage.set(null);

    // Photo upload needs an existing gardenId, but the garden doesn't exist yet at this point —
    // so only when a photo was picked do we generate one client-side and pass it through to
    // createGarden; the no-photo path is unchanged (server generates the id as it always has).
    const ids$: Observable<{ gardenId?: string; mediaId?: string }> = file
      ? (() => {
          const gardenId = generateId();
          return this.gardenApi
            .requestMediaUpload(gardenId, { contentType: file.type, fileName: file.name })
            .pipe(
              switchMap(({ uploadUrl, mediaId }) =>
                this.gardenApi
                  .uploadMedia(uploadUrl, file)
                  .pipe(map(() => ({ gardenId, mediaId }))),
              ),
            );
        })()
      : of({});

    ids$
      .pipe(
        switchMap(({ gardenId, mediaId }) =>
          this.gardenApi.createGarden({
            name,
            geolocation,
            vision: vision || undefined,
            gardenId,
            mediaId,
          }),
        ),
      )
      .subscribe({
        next: ({ gardenId }) => {
          this.currentGarden.setCurrentGardenId(gardenId);
          this.router.navigateByUrl('/home');
        },
        error: () => {
          this.submitting.set(false);
          this.errorMessage.set(
            "Couldn't save your garden. Please check your connection and try again.",
          );
        },
      });
  }
}
