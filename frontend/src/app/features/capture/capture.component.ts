import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { FindingCardComponent } from '../../shared/components/finding-card/finding-card.component';
import { ChipComponent } from '../../shared/components/chip/chip.component';
import { CaptureService } from '../../core/services/capture.service';
import { GoalApi, MockGoalApi } from '../../core/services/goal.service';

@Component({
  selector: 'td-capture',
  standalone: true,
  imports: [RouterLink, IconComponent, FindingCardComponent, ChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './capture.component.html',
  styleUrl: './capture.component.scss',
})
export class CaptureComponent {
  readonly capture = inject(CaptureService);
  private readonly goalApi = inject(GoalApi);

  readonly findings = toSignal(this.goalApi.getFindings(), { initialValue: [] });
  readonly goalFacts = toSignal(this.goalApi.getGoalFacts(), { initialValue: [] });
  readonly goalRoute = ['/goals', MockGoalApi.PRIMARY_GOAL_ID];

  runAnalysis(): void {
    this.capture.runAnalysis();
  }

  retake(): void {
    this.capture.reset();
  }
}
