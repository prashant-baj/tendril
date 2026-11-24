import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AssessmentComponent } from './assessment.component';
import { Assessment } from '../../../core/models/farm-work.model';

const CAUTION: Assessment = {
  verdict: 'caution',
  summary: 'Too close to the drench.',
  benefits: ['pest control'],
  risks: ['leaf burn'],
  conflicts: [{ with: 'Humic drench (22 Nov)', reason: '3 days apart' }],
  suggestion: 'Wait until 29 Nov.',
};

describe('AssessmentComponent', () => {
  let fixture: ComponentFixture<AssessmentComponent>;

  function render(a: Assessment | undefined): HTMLElement {
    fixture = TestBed.createComponent(AssessmentComponent);
    fixture.componentInstance.assessment = a;
    fixture.detectChanges();
    return fixture.nativeElement;
  }

  it('shows "Checking…" until the Supervisor has assessed it', () => {
    expect(render(undefined).textContent).toContain('Checking…');
  });

  it('shows the verdict and expands to the reasons', () => {
    const el = render(CAUTION);
    expect(el.textContent).toContain('Caution');
    expect(el.textContent).not.toContain('3 days apart');
    fixture.componentInstance.toggle();
    fixture.detectChanges();
    expect(el.textContent).toContain('Clashes with Humic drench (22 Nov): 3 days apart');
    expect(el.textContent).toContain('Wait until 29 Nov.');
  });

  it('opens a harmful verdict by default and marks a stale one', () => {
    const el = render({ ...CAUTION, verdict: 'harmful', stale: true });
    expect(el.querySelector('.v-harmful')).toBeTruthy();
    expect(el.textContent).toContain('Too close to the drench.');
    expect(el.textContent).toContain('updating');
  });
});
