import { ComponentFixture, TestBed } from '@angular/core/testing';
import { TraceEntryComponent } from './trace-entry.component';
import { SpecialistTraceEntry } from '../../../core/models/plan.model';

describe('TraceEntryComponent', () => {
  let fixture: ComponentFixture<TraceEntryComponent>;
  let component: TraceEntryComponent;

  const specialistEntry: SpecialistTraceEntry = {
    agent: 'agronomy',
    says: 'Nitrogen is high for a fruiting plant.',
    ms: 2100,
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TraceEntryComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(TraceEntryComponent);
    component = fixture.componentInstance;
  });

  it('renders the agent label, duration, and summary', () => {
    component.entry = specialistEntry;
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Agronomy');
    expect(text).toContain('2.1s');
    expect(text).toContain('Nitrogen is high for a fruiting plant.');
  });

  it('does not style the node as the orchestrator for a plain specialist entry', () => {
    component.entry = specialistEntry;
    fixture.detectChanges();

    const node = (fixture.nativeElement as HTMLElement).querySelector('.node');
    expect(node?.classList.contains('orchestrator')).toBe(false);
  });

  it('styles the node as the orchestrator when isOrchestrator is true', () => {
    component.entry = { agent: 'orchestrator', says: 'Feed change recommended.', ms: 900, isOrchestrator: true };
    fixture.detectChanges();

    const node = (fixture.nativeElement as HTMLElement).querySelector('.node');
    expect(node?.classList.contains('orchestrator')).toBe(true);
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Orchestrator');
  });

  it('hides the connecting line for the last entry', () => {
    component.entry = specialistEntry;
    component.last = true;
    fixture.detectChanges();

    const line = (fixture.nativeElement as HTMLElement).querySelector('.line');
    expect(line?.classList.contains('hidden')).toBe(true);
  });

  it('shows the connecting line when not the last entry', () => {
    component.entry = specialistEntry;
    component.last = false;
    fixture.detectChanges();

    const line = (fixture.nativeElement as HTMLElement).querySelector('.line');
    expect(line?.classList.contains('hidden')).toBe(false);
  });

  it('renders a short entry in full with no "Show more" toggle', () => {
    component.entry = specialistEntry;
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Nitrogen is high for a fruiting plant.');
    expect(fixture.nativeElement.querySelector('.show-more')).toBeFalsy();
  });

  it("renders the specialist's markdown as real HTML instead of raw '###'/'**' characters", () => {
    component.entry = {
      agent: 'agronomy',
      says: '### Canna Lily Care\n**Pest Control:** use neem oil.',
      ms: 2100,
    };
    fixture.detectChanges();

    const says = fixture.nativeElement.querySelector('.says') as HTMLElement;
    expect(says.innerHTML).toContain('<h3');
    expect(says.innerHTML).toContain('<strong>Pest Control:</strong>');
    expect(says.textContent).not.toContain('###');
    expect(says.textContent).not.toContain('**');
  });

  it('collapses a long entry behind a "Show more" toggle (visual clamp, full markdown always rendered), and expands on click', () => {
    // Full markdown is always parsed/rendered — collapsing never string-slices raw markdown
    // (which could cut a `**bold**`/`### heading` mid-token and render broken HTML). Only a CSS
    // class toggles, clamping the rendered height when collapsed.
    const longSays = ('**Detail.** ' + 'Detail. '.repeat(50)).trim();
    component.entry = { agent: 'agronomy', says: longSays, ms: 2100 };
    fixture.detectChanges();

    const says = () => fixture.nativeElement.querySelector('.says') as HTMLElement;
    const toggle = () => fixture.nativeElement.querySelector('.show-more') as HTMLButtonElement;

    expect(toggle()).toBeTruthy();
    expect(toggle().textContent).toContain('Show more');
    expect(says().classList.contains('clamped')).toBe(true);
    // The full markdown is rendered even while collapsed — only visually clamped by CSS.
    expect(says().innerHTML).toContain('<strong>Detail.</strong>');
    expect(says().textContent?.match(/Detail\./g)?.length).toBe(51);

    toggle().click();
    fixture.detectChanges();

    expect(says().classList.contains('clamped')).toBe(false);
    expect(toggle().textContent).toContain('Show less');

    toggle().click();
    fixture.detectChanges();

    expect(says().classList.contains('clamped')).toBe(true);
  });
});
