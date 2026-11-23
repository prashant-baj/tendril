import { HttpErrorResponse } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { FarmDetailsFieldsComponent } from '../../shared/components/farm-details/farm-details-fields.component';
import {
  buildFarmDetailsGroup,
  farmDetailsToRequest,
  patchFarmDetails,
} from '../../shared/components/farm-details/farm-details.util';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import { GardenApi } from '../../core/services/garden.service';

/**
 * Garden settings (ADR-0032): edit the vision and the farm-details `context` block of the current
 * garden through `PATCH /gardens/{gardenId}`. The context block is replaced as a whole, so the form
 * always sends the complete block (clearing a field here clears it on the server).
 */
@Component({
  selector: 'td-garden-settings',
  standalone: true,
  imports: [ReactiveFormsModule, IconComponent, FarmDetailsFieldsComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './garden-settings.component.html',
  styleUrl: './garden-settings.component.scss',
})
export class GardenSettingsComponent implements OnInit {
  private readonly gardenApi = inject(GardenApi);
  private readonly currentGarden = inject(CurrentGardenService);
  private readonly router = inject(Router);

  readonly loading = signal(true);
  readonly saving = signal(false);
  readonly gardenName = signal('');
  readonly errorMessage = signal<string | null>(null);
  readonly noGarden = signal(false);
  readonly loadFailed = signal(false);

  readonly form = new FormGroup({
    vision: new FormControl('', { nonNullable: true }),
    farm: buildFarmDetailsGroup(),
  });

  ngOnInit(): void {
    const gardenId = this.currentGarden.gardenId();
    if (!gardenId) {
      this.noGarden.set(true);
      this.loading.set(false);
      return;
    }
    // Read fresh (not the shared garden$ cache) so the form shows what's stored right now.
    this.gardenApi.getGardenById(gardenId).subscribe({
      next: (garden) => {
        this.gardenName.set(garden.name);
        this.form.controls.vision.setValue(garden.vision ?? '');
        patchFarmDetails(this.form.controls.farm, garden);
        this.loading.set(false);
      },
      error: () => {
        this.errorMessage.set("Couldn't load your garden. Please try again.");
        this.loadFailed.set(true);
        this.loading.set(false);
      },
    });
  }

  save(): void {
    const gardenId = this.currentGarden.gardenId();
    if (!gardenId || this.saving() || this.loading() || this.loadFailed()) return;
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    const farm = farmDetailsToRequest(this.form.controls.farm);
    this.saving.set(true);
    this.errorMessage.set(null);
    this.gardenApi
      .updateGarden(gardenId, {
        vision: this.form.controls.vision.value,
        climateZone: farm.climateZone,
        context: farm.context,
      })
      .subscribe({
        next: () => this.router.navigateByUrl('/garden'),
        error: (err: unknown) => {
          this.saving.set(false);
          const serverMessage =
            err instanceof HttpErrorResponse && err.status === 400
              ? (err.error as { message?: string } | null)?.message
              : undefined;
          this.errorMessage.set(
            serverMessage
              ? `Couldn't save: ${serverMessage}`
              : "Couldn't save your changes. Please check your connection and try again.",
          );
        },
      });
  }

  cancel(): void {
    this.router.navigateByUrl('/garden');
  }
}
