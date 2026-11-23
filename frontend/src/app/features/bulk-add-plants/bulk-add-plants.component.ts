import { HttpErrorResponse } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import {
  AbstractControl,
  FormControl,
  FormGroup,
  ReactiveFormsModule,
  ValidationErrors,
  Validators,
} from '@angular/forms';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import { GardenApi } from '../../core/services/garden.service';
import { BulkCreatePlantsResponse } from '../../core/models/plant.model';

export const BULK_MAX = 200;

const stakeValidators = [Validators.required, Validators.min(1), Validators.max(99999), Validators.pattern(/^\d+$/)];

/** from <= to, and at most BULK_MAX plants per call (the API's own limit). */
export function bulkRangeValidator(group: AbstractControl): ValidationErrors | null {
  const from = Number(group.get('stakeFrom')?.value);
  const to = Number(group.get('stakeTo')?.value);
  if (!from || !to) return null;
  if (from > to) return { order: true };
  if (to - from + 1 > BULK_MAX) return { tooMany: true };
  return null;
}

/**
 * "Add numbered plants" (CP-02, ADR-0032): one plant per stake number in a range, via
 * `POST /gardens/{gardenId}/plants/bulk`. Idempotent server-side — numbers already used are
 * skipped, never duplicated — so re-running after a partial failure is safe. The range is
 * pre-filled from the garden's stake range when one is set.
 */
@Component({
  selector: 'td-bulk-add-plants',
  standalone: true,
  imports: [ReactiveFormsModule, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './bulk-add-plants.component.html',
  styleUrl: './bulk-add-plants.component.scss',
})
export class BulkAddPlantsComponent implements OnInit {
  private readonly gardenApi = inject(GardenApi);
  private readonly currentGarden = inject(CurrentGardenService);
  private readonly router = inject(Router);

  readonly bulkMax = BULK_MAX;
  readonly submitting = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly result = signal<BulkCreatePlantsResponse | null>(null);

  readonly form = new FormGroup(
    {
      species: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
      variety: new FormControl('', { nonNullable: true }),
      stage: new FormControl('', { nonNullable: true, validators: [Validators.maxLength(50)] }),
      stakeFrom: new FormControl<number | null>(null, stakeValidators),
      stakeTo: new FormControl<number | null>(null, stakeValidators),
    },
    { validators: [bulkRangeValidator] },
  );

  ngOnInit(): void {
    const gardenId = this.currentGarden.gardenId();
    if (!gardenId) return;
    this.gardenApi.getGardenById(gardenId).subscribe({
      next: (garden) => {
        const range = garden.context?.stakeRange;
        // Only pre-fill an untouched form; never overwrite what the user already typed.
        if (range && this.form.pristine) {
          this.form.patchValue({ stakeFrom: range.min, stakeTo: range.max });
        }
      },
      error: () => undefined, // Pre-fill is a convenience; the form works without it.
    });
  }

  get count(): number {
    const { stakeFrom, stakeTo } = this.form.getRawValue();
    const n = Number(stakeTo) - Number(stakeFrom) + 1;
    return stakeFrom && stakeTo && n > 0 ? n : 0;
  }

  get outOfRangeCount(): number {
    return this.result()?.created.filter((c) => c.outOfRange).length ?? 0;
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

    const { species, variety, stage, stakeFrom, stakeTo } = this.form.getRawValue();
    this.submitting.set(true);
    this.errorMessage.set(null);
    this.gardenApi
      .bulkCreatePlants(gardenId, {
        species: species.trim(),
        ...(variety.trim() ? { variety: variety.trim() } : {}),
        ...(stage.trim() ? { stage: stage.trim() } : {}),
        stakeFrom: Number(stakeFrom),
        stakeTo: Number(stakeTo),
      })
      .subscribe({
        next: (res) => {
          this.submitting.set(false);
          this.result.set(res);
        },
        error: (err: unknown) => {
          this.submitting.set(false);
          const message =
            err instanceof HttpErrorResponse
              ? (err.error as { message?: string } | null)?.message
              : undefined;
          // A mid-run failure keeps what was created; running again resumes (skips those).
          this.errorMessage.set(
            message
              ? `Couldn't finish: ${message}`
              : "Couldn't add the plants. Check your connection and try again — plants already added are kept and skipped next time.",
          );
        },
      });
  }

  goToGarden(): void {
    this.router.navigateByUrl('/garden');
  }

  addMore(): void {
    this.result.set(null);
    this.form.reset();
  }
}
