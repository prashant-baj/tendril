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
});
