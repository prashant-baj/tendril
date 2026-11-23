import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Observable, of, switchMap } from 'rxjs';
import { catchError, map } from 'rxjs/operators';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { ChipComponent, ChipTone } from '../../shared/components/chip/chip.component';
import { MAX_PHOTO_BYTES } from '../../shared/components/photo-picker/photo-picker.component';
import { GardenApi, formatObservedOn } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import {
  Plant,
  PlantLogEntry,
  PlantPhoto,
  PlantStatus,
} from '../../core/models/plant.model';

const PAGE_SIZE = 20;

const KIND_LABEL: Record<PlantLogEntry['kind'], string> = {
  photo: 'Photo',
  checkin: 'Check-in',
  log: 'Note',
  retraction: 'Photo moved away',
};

const STATUS_TONE: Record<PlantStatus, ChipTone> = { healthy: 'green', ok: 'neutral', watch: 'amber' };

export interface DayGroup {
  observedOn: string;
  label: string;
  entries: PlantLogEntry[];
}

/**
 * A plant's page (CP-05, ADR-0035 §7): details, a dated photo/note timeline (newest first, paged
 * by `nextCursor`), the first and latest photo side by side, and a form to log a note/status.
 */
@Component({
  selector: 'td-plant-detail',
  standalone: true,
  imports: [RouterLink, ReactiveFormsModule, IconComponent, ChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './plant-detail.component.html',
  styleUrl: './plant-detail.component.scss',
})
export class PlantDetailComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly gardenApi = inject(GardenApi);
  private readonly currentGarden = inject(CurrentGardenService);
  private readonly fb = inject(FormBuilder);

  readonly statuses: PlantStatus[] = ['healthy', 'ok', 'watch'];
  readonly today = new Date().toISOString().slice(0, 10);

  readonly plant = signal<Plant | undefined>(undefined);
  readonly entries = signal<PlantLogEntry[]>([]);
  readonly nextCursor = signal<string | undefined>(undefined);
  /** The oldest photo, for the before → after compare. */
  readonly firstPhoto = signal<PlantPhoto | undefined>(undefined);
  readonly notFound = signal(false);
  readonly loadingMore = signal(false);
  readonly saving = signal(false);
  readonly error = signal<string | null>(null);
  readonly logPhoto = signal<File | null>(null);

  readonly logForm = this.fb.nonNullable.group({
    note: [''],
    status: [''],
    observedOn: [''],
  });

  readonly plantId = toSignal(this.route.paramMap.pipe(map((p) => p.get('plantId') ?? '')), {
    initialValue: '',
  });

  /** Entries grouped by observation date, in page order (newest first). */
  readonly days = computed<DayGroup[]>(() => {
    const groups: DayGroup[] = [];
    for (const entry of this.entries()) {
      const last = groups.at(-1);
      if (last && last.observedOn === entry.observedOn) {
        last.entries.push(entry);
      } else {
        groups.push({
          observedOn: entry.observedOn,
          label: formatObservedOn(entry.observedOn),
          entries: [entry],
        });
      }
    }
    return groups;
  });

  /** The newest photo in the loaded history — the "after" of the compare. */
  readonly latestPhoto = computed(() => this.entries().find((e) => e.photos.length)?.photos[0]);

  readonly showCompare = computed(() => {
    const first = this.firstPhoto();
    const latest = this.latestPhoto();
    return !!first && !!latest && first.mediaId !== latest.mediaId;
  });

  constructor() {
    this.route.paramMap
      .pipe(
        map((p) => p.get('plantId') ?? ''),
        switchMap((plantId) => this.load(plantId)),
        takeUntilDestroyed(),
      )
      .subscribe();
  }

  private load(plantId: string): Observable<unknown> {
    const gardenId = this.currentGarden.gardenId();
    if (!gardenId || !plantId) {
      this.notFound.set(true);
      return of(null);
    }
    this.gardenApi.getPlant(gardenId, plantId, { order: 'oldest', limit: 10 }).subscribe({
      next: (d) => this.firstPhoto.set(d.history.entries.find((e) => e.photos.length)?.photos[0]),
      error: () => this.firstPhoto.set(undefined),
    });
    return this.gardenApi.getPlant(gardenId, plantId, { limit: PAGE_SIZE }).pipe(
      map((detail) => {
        this.plant.set(detail.plant);
        this.entries.set(detail.history.entries);
        this.nextCursor.set(detail.history.nextCursor);
        this.notFound.set(false);
        return detail;
      }),
      catchError((err: unknown) => {
        if ((err as { status?: number })?.status === 404) {
          this.notFound.set(true);
          return of(null);
        }
        this.error.set("Couldn't load this plant. Try again in a moment.");
        return of(null);
      }),
    );
  }

  title(plant: Plant): string {
    return plant.stakeNo != null ? `#${plant.stakeNo} ${plant.species}` : plant.species;
  }

  /** "Note from Tendril" for Tendril's saved notes (ADR-0037), else the entry kind. */
  entryLabel(entry: PlantLogEntry): string {
    return entry.source === 'tendril' ? 'Note from Tendril' : KIND_LABEL[entry.kind];
  }

  statusTone(status: PlantStatus): ChipTone {
    return STATUS_TONE[status];
  }

  lastSeen(plant: Plant): string {
    return plant.latestObservedOn ? formatObservedOn(plant.latestObservedOn) : '';
  }

  loadMore(): void {
    const gardenId = this.currentGarden.gardenId();
    const cursor = this.nextCursor();
    if (!gardenId || !cursor || this.loadingMore()) {
      return;
    }
    this.loadingMore.set(true);
    this.gardenApi.getPlant(gardenId, this.plantId(), { limit: PAGE_SIZE, cursor }).subscribe({
      next: (detail) => {
        this.entries.update((list) => [...list, ...detail.history.entries]);
        this.nextCursor.set(detail.history.nextCursor);
        this.loadingMore.set(false);
      },
      error: () => this.loadingMore.set(false),
    });
  }

  onLogPhoto(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    input.value = '';
    if (file && file.size > MAX_PHOTO_BYTES) {
      this.error.set(
        `That photo is ${(file.size / (1024 * 1024)).toFixed(1)} MB. Photos can be up to 10 MB.`,
      );
      return;
    }
    this.error.set(null);
    this.logPhoto.set(file);
  }

  get canSave(): boolean {
    const { note, status } = this.logForm.getRawValue();
    return !!note.trim() || !!status || !!this.logPhoto();
  }

  saveLog(): void {
    const gardenId = this.currentGarden.gardenId();
    if (!gardenId || !this.canSave || this.saving()) {
      return;
    }
    const { note, status, observedOn } = this.logForm.getRawValue();
    const photo = this.logPhoto();
    this.saving.set(true);
    this.error.set(null);
    const mediaIds$: Observable<string[]> = photo
      ? this.gardenApi
          .requestMediaUpload(gardenId, {
            contentType: photo.type,
            fileName: photo.name,
            sizeBytes: photo.size,
          })
          .pipe(
            switchMap(({ uploadUrl, mediaId }) =>
              this.gardenApi.uploadMedia(uploadUrl, photo).pipe(map(() => [mediaId])),
            ),
          )
      : of([]);
    mediaIds$
      .pipe(
        switchMap((mediaIds) =>
          this.gardenApi.addPlantLog(gardenId, this.plantId(), {
            ...(note.trim() ? { note: note.trim() } : {}),
            ...(status ? { status: status as PlantStatus } : {}),
            ...(mediaIds.length ? { mediaIds } : {}),
            ...(observedOn ? { observedOn } : {}),
          }),
        ),
        // Reload page one so the new entry lands in date order and the header's latest
        // status/photo reflect it.
        switchMap(() => this.load(this.plantId())),
      )
      .subscribe({
        next: () => {
          this.saving.set(false);
          this.logForm.reset();
          this.logPhoto.set(null);
        },
        error: () => {
          this.saving.set(false);
          this.error.set("Couldn't save that. Check your connection and try again.");
        },
      });
  }

  checkThisPlant(): void {
    this.router.navigate(['/capture'], { queryParams: { plantId: this.plantId() } });
  }

  markRemoved(): void {
    const gardenId = this.currentGarden.gardenId();
    const plant = this.plant();
    if (!gardenId || !plant || plant.stage === 'removed') {
      return;
    }
    if (!window.confirm(`Mark ${this.title(plant)} as removed? Its history is kept.`)) {
      return;
    }
    this.gardenApi.updatePlantStage(gardenId, plant.plantId, 'removed').subscribe({
      next: (updated) => this.plant.set({ ...plant, stage: updated.stage }),
      error: () => this.error.set("Couldn't update the plant. Try again."),
    });
  }
}
