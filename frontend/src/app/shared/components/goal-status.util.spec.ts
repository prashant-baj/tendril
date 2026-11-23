import { goalNeedsAttention, goalStatusLabel, goalStatusTone } from './goal-status.util';

describe('goal status presentation (ADR-0036)', () => {
  it('shows InformationSaved as a neutral "Saved" that needs no attention', () => {
    expect(goalStatusLabel('InformationSaved')).toBe('Saved');
    expect(goalStatusTone('InformationSaved')).toBe('neutral');
    expect(goalNeedsAttention('InformationSaved')).toBeFalse();
  });

  it('keeps the existing statuses as they were', () => {
    expect(goalStatusLabel('PlanProposed')).toBe('PlanProposed');
    expect(goalStatusTone('PlanProposed')).toBe('amber');
    expect(goalNeedsAttention('PlanProposed')).toBeTrue();
    expect(goalNeedsAttention('Decomposing')).toBeTrue();
    expect(goalStatusTone('Approved')).toBe('green');
    expect(goalNeedsAttention('Approved')).toBeFalse();
  });
});
