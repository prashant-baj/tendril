import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { Observable, concatMap, from, map, of, toArray } from 'rxjs';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { AssessmentComponent } from '../../shared/components/assessment/assessment.component';
import {
  MAX_PHOTO_BYTES,
  MAX_VIDEO_BYTES,
} from '../../shared/components/photo-picker/photo-picker.component';
import { FarmWorkApi } from '../../core/services/farm-work.service';
import { GardenApi, formatObservedOn } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import {
  Activity,
  ActivityLog,
  ActivityKind,
  GardenAction,
  Schedule,
  Targets,
} from '../../core/models/farm-work.model';
import { Plant } from '../../core/models/plant.model';

type FormMode = 'none' | 'log' | 'plan';
const MAX_FILES = 6;
// The Supervisor assesses asynchronously (ADR-0039); refresh a couple of times after a save.
const REFRESH_AFTER_MS = [15000, 45000];

/**
 * Farm work (ADR-0038): planned and recurring work with what's due, a log of work done (with
 * photos/videos), the Supervisor's verdict on each (ADR-0039) and its suggestions to confirm.
 */
@Component({
  selector: 'td-farm-work',
  standalone: true,
  imports: [ReactiveFormsModule, IconComponent, AssessmentComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './farm-work.component.html',
  styleUrl: './farm-work.component.scss',
})
export class FarmWorkComponent implements OnInit {
  private readonly api = inject(FarmWorkApi);
  private readonly gardenApi = inject(GardenApi);
  private readonly currentGarden = inject(CurrentGardenService);
  private readonly fb = inject(FormBuilder);
  private readonly destroyRef = inject(DestroyRef);

  readonly today = new Date().toISOString().slice(0, 10);
  readonly activities = signal<Activity[]>([]);
  readonly suggestions = signal<GardenAction[]>([]);
  readonly logs = signal<ActivityLog[]>([]);
  readonly nextCursor = signal<string | undefined>(undefined);
  readonly plants = signal<Plant[]>([]);
  readonly mode = signal<FormMode>('none');
  /** The planned activity being logged ("Log it"), if any. */
  readonly logging = signal<Activity | null>(null);
  readonly files = signal<File[]>([]);
  readonly saving = signal(false);
  readonly busyId = signal<string | null>(null);
  readonly error = signal<string | null>(null);

  readonly open = computed(() =>
    this.activities().filter((a) => a.status !== 'done' && a.status !== 'archived'),
  );

  readonly form = this.fb.nonNullable.group({
    title: [''],
    kind: ['input' as ActivityKind],
    product: [''],
    dose: [''],
    details: [''],
    targets: ['all'],
    date: [''],
    note: [''],
    repeat: ['none' as 'none' | 'every' | 'window'],
    everyDays: [7],
    minDays: [21],
    maxDays: [28],
  });

  private readonly timers: ReturnType<typeof setTimeout>[] = [];

  constructor() {
    this.destroyRef.onDestroy(() => this.timers.forEach((t) => clearTimeout(t)));
  }

  ngOnInit(): void {
    this.reload();
    const gardenId = this.currentGarden.gardenId();
    this.gardenApi.getPlants(gardenId).subscribe((plants) => this.plants.set(plants));
  }

  reload(): void {
    const gardenId = this.currentGarden.gardenId();
    if (!gardenId) {
      return;
    }
    this.api.listActivities(gardenId).subscribe((a) => this.activities.set(a));
    this.api.listSuggestions(gardenId).subscribe((s) => this.suggestions.set(s));
    this.api.listLogs(gardenId).subscribe((page) => {
      this.logs.set(page.logs);
      this.nextCursor.set(page.nextCursor);
    });
  }

  private refreshSoon(): void {
    for (const ms of REFRESH_AFTER_MS) {
      this.timers.push(setTimeout(() => this.reload(), ms));
    }
  }

  loadOlder(): void {
    const gardenId = this.currentGarden.gardenId();
    const cursor = this.nextCursor();
    if (!gardenId || !cursor) {
      return;
    }
    this.api.listLogs(gardenId, cursor).subscribe((page) => {
      this.logs.update((list) => [...list, ...page.logs]);
      this.nextCursor.set(page.nextCursor);
    });
  }

  // --- presentation ----------------------------------------------------------------------------

  dueLabel(a: Activity): string {
    switch (a.dueState) {
      case 'overdue':
        return `Overdue since ${formatObservedOn(a.overdueOn ?? a.nextDueOn ?? '')}`;
      case 'due':
        return 'Due now';
      case 'upcoming':
        return `Next ${formatObservedOn(a.nextDueOn ?? '')}`;
      case 'paused':
        return 'Paused';
      default:
        return 'Not scheduled';
    }
  }

  scheduleLabel(schedule: Schedule | undefined): string {
    if (!schedule) {
      return 'One-off';
    }
    return schedule.everyDays
      ? `Every ${schedule.everyDays} days`
      : `Every ${schedule.minDays}–${schedule.maxDays} days`;
  }

  targetsLabel(targets: Targets): string {
    if (targets === 'all') {
      return 'Whole garden';
    }
    const byId = new Map(this.plants().map((p) => [p.plantId, p]));
    return targets.map((id) => (byId.get(id)?.stakeNo != null ? `#${byId.get(id)!.stakeNo}` : 'a plant')).join(', ');
  }

  date(day: string): string {
    return formatObservedOn(day);
  }

  // --- forms -------------------------------------------------------------------------------------

  startLog(activity: Activity | null = null): void {
    if (activity?.assessment?.verdict === 'harmful') {
      // Owner decision (ADR-0038 review): highlight harmful work AND ask before recording it.
      const ok = window.confirm(
        `Tendril flagged "${activity.title}" as harmful: ${activity.assessment.summary}\n\nLog it anyway?`,
      );
      if (!ok) {
        return;
      }
    }
    this.logging.set(activity);
    this.files.set([]);
    this.form.reset();
    if (activity) {
      this.form.patchValue({
        title: activity.title,
        kind: activity.kind,
        product: activity.product ?? '',
        dose: activity.dose ?? '',
        details: activity.details ?? '',
        targets: this.targetsText(activity.targets),
      });
    }
    this.form.patchValue({ date: this.today });
    this.mode.set('log');
  }

  startPlan(): void {
    this.logging.set(null);
    this.form.reset();
    this.form.patchValue({ date: this.today });
    this.mode.set('plan');
  }

  cancel(): void {
    this.mode.set('none');
    this.error.set(null);
  }

  private targetsText(targets: Targets): string {
    if (targets === 'all') {
      return 'all';
    }
    return this.targetsLabel(targets).replace(/#/g, '');
  }

  /** "all" or stake numbers like "94, 95, 102" -> plant ids; null if a number isn't in the garden. */
  parseTargets(text: string): Targets | null {
    const value = text.trim().toLowerCase();
    if (!value || value === 'all') {
      return 'all';
    }
    const byStake = new Map(this.plants().filter((p) => p.stakeNo != null).map((p) => [p.stakeNo!, p.plantId]));
    const ids: string[] = [];
    for (const part of value.split(/[\s,]+/).filter(Boolean)) {
      const id = byStake.get(Number(part.replace('#', '')));
      if (!id) {
        return null;
      }
      ids.push(id);
    }
    return ids.length ? ids : 'all';
  }

  onFiles(event: Event): void {
    const input = event.target as HTMLInputElement;
    const picked = Array.from(input.files ?? []);
    input.value = '';
    for (const f of picked) {
      const video = f.type.startsWith('video/');
      if (video && !['video/mp4', 'video/quicktime'].includes(f.type)) {
        this.error.set('Videos must be MP4 or MOV.');
        return;
      }
      if (f.size > (video ? MAX_VIDEO_BYTES : MAX_PHOTO_BYTES)) {
        this.error.set(video ? 'Videos can be up to 100 MB.' : 'Photos can be up to 10 MB.');
        return;
      }
    }
    this.error.set(null);
    this.files.update((list) => [...list, ...picked].slice(0, MAX_FILES));
  }

  removeFile(index: number): void {
    this.files.update((list) => list.filter((_, i) => i !== index));
  }

  private upload(gardenId: string): Observable<string[]> {
    const files = this.files();
    if (!files.length) {
      return of([]);
    }
    return from(files).pipe(
      concatMap((file) =>
        this.gardenApi
          .requestMediaUpload(gardenId, { contentType: file.type, fileName: file.name, sizeBytes: file.size })
          .pipe(concatMap(({ uploadUrl, mediaId }) => this.gardenApi.uploadMedia(uploadUrl, file).pipe(map(() => mediaId)))),
      ),
      toArray(),
    );
  }

  save(): void {
    const gardenId = this.currentGarden.gardenId();
    const v = this.form.getRawValue();
    if (!gardenId || this.saving()) {
      return;
    }
    if (!v.title.trim()) {
      this.error.set('Give the work a name.');
      return;
    }
    const targets = this.parseTargets(v.targets);
    if (!targets) {
      this.error.set('Use "all" or plant numbers from this garden, e.g. 94, 95.');
      return;
    }
    const work = {
      title: v.title.trim(),
      kind: v.kind,
      ...(v.product.trim() ? { product: v.product.trim() } : {}),
      ...(v.dose.trim() ? { dose: v.dose.trim() } : {}),
      ...(v.details.trim() ? { details: v.details.trim() } : {}),
      targets,
    };
    this.saving.set(true);
    this.error.set(null);
    const request$: Observable<unknown> =
      this.mode() === 'plan'
        ? this.api.createActivity(gardenId, {
            ...work,
            schedule:
              v.repeat === 'every'
                ? { everyDays: v.everyDays }
                : v.repeat === 'window'
                  ? { minDays: v.minDays, maxDays: v.maxDays }
                  : null,
            ...(v.date ? { startOn: v.date } : {}),
          })
        : this.upload(gardenId).pipe(
            concatMap((mediaIds) =>
              this.api.logActivity(gardenId, {
                ...work,
                ...(this.logging() ? { activityId: this.logging()!.activityId } : {}),
                ...(v.date ? { doneOn: v.date } : {}),
                ...(v.note.trim() ? { note: v.note.trim() } : {}),
                ...(mediaIds.length ? { mediaIds } : {}),
              }),
            ),
          );
    request$.subscribe({
      next: () => {
        this.saving.set(false);
        this.mode.set('none');
        this.reload();
        this.refreshSoon();
      },
      error: () => {
        this.saving.set(false);
        this.error.set("Couldn't save that. Check your connection and try again.");
      },
    });
  }

  // --- activity actions + suggestions -------------------------------------------------------

  setStatus(a: Activity, status: 'active' | 'paused' | 'archived'): void {
    const gardenId = this.currentGarden.gardenId();
    if (!gardenId) {
      return;
    }
    if (status === 'archived' && !window.confirm(`Archive "${a.title}"? Its logs are kept.`)) {
      return;
    }
    this.busyId.set(a.activityId);
    this.api.updateActivity(gardenId, a.activityId, { status }).subscribe({
      next: () => {
        this.busyId.set(null);
        this.reload();
      },
      error: () => this.busyId.set(null),
    });
  }

  resolve(s: GardenAction, confirm: boolean): void {
    const gardenId = this.currentGarden.gardenId();
    if (!gardenId || this.busyId()) {
      return;
    }
    this.busyId.set(s.actionId);
    const call$ = confirm
      ? this.api.confirmSuggestion(gardenId, s.actionId)
      : this.api.rejectSuggestion(gardenId, s.actionId);
    call$.subscribe({
      next: () => {
        this.busyId.set(null);
        this.reload();
        if (confirm) {
          this.refreshSoon();
        }
      },
      error: () => {
        this.busyId.set(null);
        this.error.set('That suggestion no longer applies.');
        this.reload();
      },
    });
  }
}
