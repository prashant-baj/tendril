import { CurrentGardenService } from './current-garden.service';

describe('CurrentGardenService', () => {
  beforeEach(() => localStorage.removeItem('tendril:currentGardenId'));
  afterEach(() => localStorage.removeItem('tendril:currentGardenId'));

  it('starts with no current garden when nothing is persisted', () => {
    expect(new CurrentGardenService().gardenId()).toBeNull();
  });

  it('setCurrentGardenId() updates the signal and persists it', () => {
    const service = new CurrentGardenService();
    service.setCurrentGardenId('g-1');
    expect(service.gardenId()).toBe('g-1');
    expect(localStorage.getItem('tendril:currentGardenId')).toBe('g-1');
  });

  it('picks up a persisted id on construction', () => {
    localStorage.setItem('tendril:currentGardenId', 'g-2');
    expect(new CurrentGardenService().gardenId()).toBe('g-2');
  });
});
