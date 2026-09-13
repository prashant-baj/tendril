import { weatherPresentation } from './weather-presentation.util';

describe('weatherPresentation', () => {
  it('maps clear sky (0)', () => {
    expect(weatherPresentation(0)).toEqual({ label: 'clear', icon: 'sunny' });
  });

  it('maps partly cloudy (1-2)', () => {
    expect(weatherPresentation(1).label).toBe('partly cloudy');
    expect(weatherPresentation(2).label).toBe('partly cloudy');
  });

  it('maps overcast (3)', () => {
    expect(weatherPresentation(3)).toEqual({ label: 'cloudy', icon: 'cloud' });
  });

  it('maps fog (45, 48)', () => {
    expect(weatherPresentation(45).label).toBe('foggy');
    expect(weatherPresentation(48).label).toBe('foggy');
  });

  it('maps rain codes (51-67)', () => {
    expect(weatherPresentation(61).label).toBe('rainy');
  });

  it('maps snow codes (71-77)', () => {
    expect(weatherPresentation(73).label).toBe('snowy');
  });

  it('maps thunderstorm codes (95+)', () => {
    expect(weatherPresentation(95).label).toBe('stormy');
    expect(weatherPresentation(99).label).toBe('stormy');
  });

  it('falls back to a generic presentation for an unrecognized code', () => {
    expect(weatherPresentation(9999)).toEqual({ label: 'mild', icon: 'partly_cloudy_day' });
  });
});
