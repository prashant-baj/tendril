import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { CaptureComponent } from './capture.component';
import { GoalApi, MockGoalApi } from '../../core/services/goal.service';

describe('CaptureComponent', () => {
  let fixture: ComponentFixture<CaptureComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [CaptureComponent],
      providers: [provideRouter([]), { provide: GoalApi, useClass: MockGoalApi }],
    }).compileComponents();
    fixture = TestBed.createComponent(CaptureComponent);
    fixture.detectChanges();
  });

  it('starts idle with the "Analyse this photo" CTA', () => {
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Analyse this photo');
  });

  it('runAnalysis() switches the capture service into analyzing', () => {
    fixture.componentInstance.runAnalysis();
    expect(fixture.componentInstance.capture.phase()).toBe('analyzing');
  });
});
