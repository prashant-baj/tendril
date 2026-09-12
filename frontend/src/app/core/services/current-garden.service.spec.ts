import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';
import { CurrentGardenService } from './current-garden.service';
import { GardenApi, MockGardenApi } from './garden.service';

describe('CurrentGardenService', () => {
  beforeEach(() => {
    localStorage.removeItem('tendril:currentGardenId');
    TestBed.configureTestingModule({
      providers: [{ provide: GardenApi, useClass: MockGardenApi }],
    });
  });
  afterEach(() => localStorage.removeItem('tendril:currentGardenId'));

  it('starts with no current garden when nothing is persisted', () => {
    expect(TestBed.inject(CurrentGardenService).gardenId()).toBeNull();
  });

  it('setCurrentGardenId() updates the signal and persists it', () => {
    const service = TestBed.inject(CurrentGardenService);
    service.setCurrentGardenId('g-1');
    expect(service.gardenId()).toBe('g-1');
    expect(localStorage.getItem('tendril:currentGardenId')).toBe('g-1');
  });

  it('picks up a persisted id on construction', () => {
    localStorage.setItem('tendril:currentGardenId', 'g-2');
    expect(TestBed.inject(CurrentGardenService).gardenId()).toBe('g-2');
  });

  it('garden$ falls back to getGarden() (the default fixture) when no garden is current', async () => {
    const service = TestBed.inject(CurrentGardenService);
    const garden = await firstValueFrom(service.garden$);
    expect(garden.gardenId).toBe('balcony-kitchen-garden');
  });

  it('garden$ resolves the real created garden once one is set as current', async () => {
    const gardenApi = TestBed.inject(GardenApi);
    const { gardenId } = await firstValueFrom(
      gardenApi.createGarden({ name: 'Terrace Garden', geolocation: 'Mumbai' }),
    );
    const service = TestBed.inject(CurrentGardenService);
    service.setCurrentGardenId(gardenId);

    const garden = await firstValueFrom(service.garden$);
    expect(garden).toEqual({
      gardenId,
      name: 'Terrace Garden',
      vision: '',
      geolocation: 'Mumbai',
      climateZone: '',
    });
  });
});
