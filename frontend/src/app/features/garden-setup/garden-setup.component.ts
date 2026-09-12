import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import { GardenApi } from '../../core/services/garden.service';

/** OB-01 "Setup My Garden": name / geolocation (required), vision (optional). */
@Component({
  selector: 'td-garden-setup',
  standalone: true,
  imports: [ReactiveFormsModule, IconComponent],
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

  readonly form = this.fb.nonNullable.group({
    name: ['', [Validators.required, Validators.minLength(1)]],
    geolocation: ['', [Validators.required, Validators.minLength(1)]],
    vision: [''],
  });

  submit(): void {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }

    const { name, geolocation, vision } = this.form.getRawValue();
    this.submitting.set(true);
    this.errorMessage.set(null);

    this.gardenApi.createGarden({ name, geolocation, vision: vision || undefined }).subscribe({
      next: ({ gardenId }) => {
        this.currentGarden.setCurrentGardenId(gardenId);
        this.router.navigateByUrl('/home');
      },
      error: () => {
        this.submitting.set(false);
        this.errorMessage.set("Couldn't save your garden. Please check your connection and try again.");
      },
    });
  }
}
