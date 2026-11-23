import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Observable, interval, of, startWith, switchMap, takeWhile } from 'rxjs';
import { map } from 'rxjs/operators';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { ChipComponent, ChipTone } from '../../shared/components/chip/chip.component';
import { PlanTaskCardComponent } from '../../shared/components/plan-task-card/plan-task-card.component';
import { TraceEntryComponent } from '../../shared/components/trace-entry/trace-entry.component';
import { ActionProposalComponent } from '../../shared/components/action-proposal/action-proposal.component';
import { MarkdownPipe } from '../../shared/pipes/markdown.pipe';
import { GoalApi, GoalDetail } from '../../core/services/goal.service';
import { ChatMessage, GoalAction } from '../../core/models/message.model';
import { MAX_PHOTO_BYTES } from '../../shared/components/photo-picker/photo-picker.component';
import { GardenApi } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';

const IN_PROGRESS_STATUSES = new Set(['Approved', 'InProgress']);

// After sending a message, poll for the orchestrator's (asynchronous) reply every 2s — no
// WebSocket channel exists yet (plan-approval.md's PA-02 Context note), so this is how the
// chat becomes two-way without the gardener having to manually refresh the page. Gives up
// after ~this many polls so a stuck orchestrator invocation doesn't poll forever.
const POLL_INTERVAL_MS = 2000;
const MAX_POLLS = 30; // ~60s

@Component({
  selector: 'td-goal-detail',
  standalone: true,
  imports: [
    RouterLink,
    ReactiveFormsModule,
    IconComponent,
    ChipComponent,
    PlanTaskCardComponent,
    TraceEntryComponent,
    ActionProposalComponent,
    MarkdownPipe,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './goal-detail.component.html',
  styleUrl: './goal-detail.component.scss',
})
export class GoalDetailComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly goalApi = inject(GoalApi);
  private readonly gardenApi = inject(GardenApi);
  private readonly currentGarden = inject(CurrentGardenService);
  private readonly fb = inject(FormBuilder);
  private readonly destroyRef = inject(DestroyRef);

  private readonly paramMap = toSignal(this.route.paramMap, { initialValue: undefined });
  private readonly goalId = computed(() => this.paramMap()?.get('goalId') ?? '');

  private readonly detailSignal = signal<GoalDetail | undefined>(undefined);
  readonly detail = this.detailSignal.asReadonly();

  readonly sending = signal(false);
  readonly approving = signal(false);
  // True while polling for the orchestrator's reply after sending a message — drives the
  // "Tendril is typing…" indicator so the chat reads as live, two-way conversation.
  readonly waitingForReply = signal(false);
  // The taskId currently uploading a check-in photo, if any — lets the template disable just
  // that one task's picker rather than the whole plan.
  readonly checkingInTaskId = signal<string | null>(null);
  // The taskId waiting on the orchestrator's async check-in feedback (PA-05), if any — drives
  // that task's "Tendril is reviewing your check-in…" line.
  readonly awaitingFeedbackForTaskId = signal<string | null>(null);
  // "How this was decided" (PA-05) — collapsed by default, same as the original mockup.
  readonly traceOpen = signal(false);

  // ADR-0034: text is optional when the message carries a photo or checks in on a task.
  readonly replyForm = this.fb.nonNullable.group({
    content: [''],
    taskId: [''],
  });
  readonly chatPhoto = signal<File | null>(null);
  readonly chatError = signal<string | null>(null);
  // The action currently being confirmed/dismissed, so only its buttons disable.
  readonly actionBusy = signal<string | null>(null);
  readonly actionError = signal<string | null>(null);

  readonly pendingTasks = computed(() =>
    (this.detail()?.tasks ?? []).filter((t) => t.status !== 'done'),
  );

  constructor() {
    // Re-fetch whenever the garden or goal changes (e.g. navigating between goals).
    effect(() => {
      const gardenId = this.currentGarden.gardenId();
      const goalId = this.goalId();
      if (goalId) {
        this.goalApi
          .getGoalDetail(gardenId, goalId)
          .subscribe((d) => this.detailSignal.set(d));
      }
    });
  }

  get planStatusTone(): ChipTone {
    const plan = this.detail()?.plan;
    return plan && IN_PROGRESS_STATUSES.has(plan.status) ? 'green' : 'amber';
  }

  get planIsApproved(): boolean {
    const plan = this.detail()?.plan;
    return !!plan && IN_PROGRESS_STATUSES.has(plan.status);
  }

  get traceSpecialistCount(): number {
    return (this.detail()?.plan?.trace ?? []).filter((e) => !e.isOrchestrator).length;
  }

  get traceTotalSeconds(): number {
    const totalMs = (this.detail()?.plan?.trace ?? []).reduce((sum, e) => sum + e.ms, 0);
    return Math.round(totalMs / 1000);
  }

  toggleTrace(): void {
    this.traceOpen.update((open) => !open);
  }

  onImageError(event: Event): void {
    (event.target as HTMLImageElement).style.display = 'none';
  }

  /** The proposal an assistant message asks about, if any (ADR-0034). */
  actionFor(message: ChatMessage): GoalAction | undefined {
    return message.actionId
      ? this.detail()?.actions?.find((a) => a.actionId === message.actionId)
      : undefined;
  }

  taskTitle(taskId: string | undefined): string {
    return this.detail()?.tasks.find((t) => t.taskId === taskId)?.title ?? '';
  }

  get canSend(): boolean {
    const { content, taskId } = this.replyForm.getRawValue();
    return !!content.trim() || !!this.chatPhoto() || !!taskId;
  }

  onChatPhoto(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    input.value = '';
    if (file && file.size > MAX_PHOTO_BYTES) {
      this.chatError.set(
        `That photo is ${(file.size / (1024 * 1024)).toFixed(1)} MB. Photos can be up to 10 MB.`,
      );
      return;
    }
    this.chatError.set(null);
    this.chatPhoto.set(file);
  }

  removeChatPhoto(): void {
    this.chatPhoto.set(null);
  }

  sendMessage(): void {
    if (!this.canSend || this.sending()) {
      return;
    }
    const gardenId = this.currentGarden.gardenId();
    const goalId = this.goalId();
    if (!gardenId) {
      return;
    }
    const { content, taskId } = this.replyForm.getRawValue();
    const photo = this.chatPhoto();
    // Captured before sending: the reply hasn't been written yet, so "the user's message plus
    // one assistant reply" is exactly messagesBefore + 2 — that's what polling waits for.
    const messagesBefore = this.detail()?.messages.length ?? 0;
    this.sending.set(true);
    this.chatError.set(null);
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
          this.goalApi.sendMessage(gardenId, goalId, content.trim(), {
            mediaIds,
            taskId: taskId || undefined,
          }),
        ),
      )
      .subscribe({
        next: () => {
          this.sending.set(false);
          this.replyForm.reset();
          this.chatPhoto.set(null);
          this.pollForReply(gardenId, goalId, messagesBefore);
        },
        error: () => {
          this.sending.set(false);
          this.chatError.set("Couldn't send that. Please check your connection and try again.");
        },
      });
  }

  /** ADR-0034: the only way a proposed change is applied — a tap, never a typed "yes". */
  confirmAction(action: GoalAction): void {
    this.resolveAction(action, (g, goal) => this.goalApi.confirmAction(g, goal, action.actionId));
  }

  rejectAction(action: GoalAction): void {
    this.resolveAction(action, (g, goal) => this.goalApi.rejectAction(g, goal, action.actionId));
  }

  private resolveAction(
    action: GoalAction,
    call: (gardenId: string, goalId: string) => Observable<void>,
  ): void {
    const gardenId = this.currentGarden.gardenId();
    const goalId = this.goalId();
    if (!gardenId || this.actionBusy()) {
      return;
    }
    this.actionBusy.set(action.actionId);
    this.actionError.set(null);
    call(gardenId, goalId)
      .pipe(switchMap(() => this.goalApi.getGoalDetail(gardenId, goalId)))
      .subscribe({
        next: (d) => {
          this.actionBusy.set(null);
          this.detailSignal.set(d);
        },
        error: (err: unknown) => {
          this.actionBusy.set(null);
          const status = (err as { status?: number } | null)?.status;
          this.actionError.set(
            status === 409
              ? 'That no longer applies — it may already be done.'
              : "Couldn't save that. Please try again.",
          );
          this.goalApi.getGoalDetail(gardenId, goalId).subscribe((d) => this.detailSignal.set(d));
        },
      });
  }

  private pollForReply(
    gardenId: string,
    goalId: string,
    messagesBefore: number,
    onDone?: () => void,
  ): void {
    this.waitingForReply.set(true);
    let attempts = 0;
    interval(POLL_INTERVAL_MS)
      .pipe(
        startWith(0),
        switchMap(() => this.goalApi.getGoalDetail(gardenId, goalId)),
        takeWhile((d) => {
          attempts += 1;
          const gotReply = (d?.messages.length ?? 0) >= messagesBefore + 2;
          return !gotReply && attempts < MAX_POLLS;
        }, true),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: (d) => this.detailSignal.set(d),
        complete: () => {
          this.waitingForReply.set(false);
          onDone?.();
        },
      });
  }

  approve(): void {
    const gardenId = this.currentGarden.gardenId();
    const plan = this.detail()?.plan;
    if (!gardenId || !plan || this.approving()) {
      return;
    }
    this.approving.set(true);
    this.goalApi.approve(gardenId, plan.planId).subscribe({
      next: () => {
        this.approving.set(false);
        this.goalApi
          .getGoalDetail(gardenId, this.goalId())
          .subscribe((d) => this.detailSignal.set(d));
      },
      error: () => this.approving.set(false),
    });
  }

  onTaskCheckin(taskId: string, file: File): void {
    const gardenId = this.currentGarden.gardenId();
    const goalId = this.goalId();
    if (!gardenId || this.checkingInTaskId()) {
      return;
    }
    this.checkingInTaskId.set(taskId);
    const messagesBefore = this.detail()?.messages.length ?? 0;
    this.gardenApi
      .requestMediaUpload(gardenId, { contentType: file.type, fileName: file.name, sizeBytes: file.size })
      .pipe(
        switchMap(({ uploadUrl, mediaId }) =>
          this.gardenApi.uploadMedia(uploadUrl, file).pipe(map(() => mediaId)),
        ),
        switchMap((mediaId) => this.goalApi.checkinTask(gardenId, goalId, taskId, mediaId)),
      )
      .subscribe({
        next: () => {
          this.checkingInTaskId.set(null);
          // ADR-0034: a check-in is a chat turn now — the check-in message plus Tendril's reply
          // (with a Confirm button) is what we wait for, not task feedback.
          this.awaitingFeedbackForTaskId.set(taskId);
          this.pollForReply(gardenId, goalId, messagesBefore, () =>
            this.awaitingFeedbackForTaskId.set(null),
          );
        },
        error: () => this.checkingInTaskId.set(null),
      });
  }
}
