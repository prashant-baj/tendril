import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { interval, startWith, switchMap, takeWhile } from 'rxjs';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { ChipComponent, ChipTone } from '../../shared/components/chip/chip.component';
import { PlanTaskCardComponent } from '../../shared/components/plan-task-card/plan-task-card.component';
import { MarkdownPipe } from '../../shared/pipes/markdown.pipe';
import { GoalApi, GoalDetail } from '../../core/services/goal.service';
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
  imports: [RouterLink, ReactiveFormsModule, IconComponent, ChipComponent, PlanTaskCardComponent, MarkdownPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './goal-detail.component.html',
  styleUrl: './goal-detail.component.scss',
})
export class GoalDetailComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly goalApi = inject(GoalApi);
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

  readonly replyForm = this.fb.nonNullable.group({
    content: ['', [Validators.required, Validators.minLength(1)]],
  });

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

  onImageError(event: Event): void {
    (event.target as HTMLImageElement).style.display = 'none';
  }

  sendMessage(): void {
    if (this.replyForm.invalid || this.sending()) {
      this.replyForm.markAllAsTouched();
      return;
    }
    const gardenId = this.currentGarden.gardenId();
    const goalId = this.goalId();
    if (!gardenId) {
      return;
    }
    const { content } = this.replyForm.getRawValue();
    // Captured before sending: the reply hasn't been written yet, so "the user's message plus
    // one assistant reply" is exactly messagesBefore + 2 — that's what polling waits for.
    const messagesBefore = this.detail()?.messages.length ?? 0;
    this.sending.set(true);
    this.goalApi.sendMessage(gardenId, goalId, content).subscribe({
      next: () => {
        this.sending.set(false);
        this.replyForm.reset();
        this.pollForReply(gardenId, goalId, messagesBefore);
      },
      error: () => this.sending.set(false),
    });
  }

  private pollForReply(gardenId: string, goalId: string, messagesBefore: number): void {
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
        complete: () => this.waitingForReply.set(false),
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
}
