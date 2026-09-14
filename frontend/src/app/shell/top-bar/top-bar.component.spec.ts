import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Observable, of } from 'rxjs';
import { TopBarComponent } from './top-bar.component';
import { GardenApi, MockGardenApi } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import { Garden, GardenSummary, GardenWeather } from '../../core/models/garden.model';

describe('TopBarComponent', () => {
  let fixture: ComponentFixture<TopBarComponent>;

  beforeEach(() => localStorage.removeItem('tendril:currentGardenId'));
  afterEach(() => localStorage.removeItem('tendril:currentGardenId'));

  async function setup(gardenApi: Partial<GardenApi>) {
    await TestBed.configureTestingModule({
      imports: [TopBarComponent],
      providers: [provideRouter([]), { provide: GardenApi, useValue: gardenApi }],
    }).compileComponents();
    fixture = TestBed.createComponent(TopBarComponent);
    fixture.detectChanges();
  }

  it('shows the real created garden name once one exists', async () => {
    const garden: Garden = {
      gardenId: 'g-1',
      name: 'My Terrace Garden',
      geolocation: 'Mumbai',
      vision: '',
      climateZone: '',
    };
    await setup({ getGarden: () => of(garden) });

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('My Terrace Garden');
    expect(text).not.toContain('Balcony Kitchen Garden');
  });

  it('falls back to a neutral label before any garden data resolves', async () => {
    // A GardenApi whose getGarden() never emits (simulates the async gap before first value).
    await setup({ getGarden: () => new Observable<Garden>(() => {}) });
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('My Garden');
  });

  it('does not hardcode a garden name anywhere in the template', async () => {
    await setup(new MockGardenApi());
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    // MockGardenApi's default fixture is "Balcony Kitchen Garden" — proves this came from the
    // service, not a literal string still baked into the component template.
    expect(text).toContain('Balcony Kitchen Garden');
  });
});

describe('TopBarComponent garden switcher', () => {
  let fixture: ComponentFixture<TopBarComponent>;

  const gardens: GardenSummary[] = [
    { gardenId: 'g-1', name: 'My Terrace Garden' },
    { gardenId: 'g-2', name: 'Balcony Garden' },
  ];

  beforeEach(async () => {
    localStorage.removeItem('tendril:currentGardenId');
    await TestBed.configureTestingModule({
      imports: [TopBarComponent],
      providers: [
        provideRouter([]),
        {
          provide: GardenApi,
          useValue: {
            getGarden: () =>
              of({ gardenId: 'g-1', name: 'My Terrace Garden', geolocation: '', vision: '', climateZone: '' }),
            getGardens: () => of(gardens),
            getGardenWeather: () => of({ temperatureC: 21, weatherCode: 1 }),
          },
        },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(TopBarComponent);
    fixture.detectChanges();
  });

  afterEach(() => localStorage.removeItem('tendril:currentGardenId'));

  it('is closed by default and does not fetch the garden list', () => {
    expect(fixture.componentInstance.dropdownOpen()).toBe(false);
    expect(fixture.nativeElement.querySelector('.garden-dropdown')).toBeFalsy();
  });

  it('fetches and opens the garden list when the switch is clicked', () => {
    fixture.componentInstance.toggleGardenDropdown();
    fixture.detectChanges();

    expect(fixture.componentInstance.dropdownOpen()).toBe(true);
    const options = fixture.nativeElement.querySelectorAll('.garden-option');
    expect(options.length).toBe(2);
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Balcony Garden');
  });

  it('closes the dropdown when the switch is clicked again', () => {
    fixture.componentInstance.toggleGardenDropdown();
    fixture.componentInstance.toggleGardenDropdown();
    fixture.detectChanges();

    expect(fixture.componentInstance.dropdownOpen()).toBe(false);
  });

  it('switching gardens sets the current garden id and closes the dropdown', () => {
    const currentGarden = TestBed.inject(CurrentGardenService);
    spyOn(currentGarden, 'setCurrentGardenId');

    fixture.componentInstance.toggleGardenDropdown();
    fixture.componentInstance.selectGarden('g-2');

    expect(currentGarden.setCurrentGardenId).toHaveBeenCalledWith('g-2');
    expect(fixture.componentInstance.dropdownOpen()).toBe(false);
  });

  it('closes the dropdown when clicking outside the component', () => {
    fixture.componentInstance.toggleGardenDropdown();
    fixture.detectChanges();

    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(fixture.componentInstance.dropdownOpen()).toBe(false);
  });
});

describe('TopBarComponent weather chip', () => {
  let fixture: ComponentFixture<TopBarComponent>;
  const garden: Garden = {
    gardenId: 'g-1',
    name: 'My Terrace Garden',
    geolocation: 'Pune',
    vision: '',
    climateZone: '',
  };

  beforeEach(() => localStorage.setItem('tendril:currentGardenId', 'g-1'));
  afterEach(() => localStorage.removeItem('tendril:currentGardenId'));

  async function setup(weather$: Observable<GardenWeather | undefined>) {
    await TestBed.configureTestingModule({
      imports: [TopBarComponent],
      providers: [
        provideRouter([]),
        {
          provide: GardenApi,
          useValue: {
            getGardenById: () => of(garden),
            getGardenWeather: () => weather$,
          },
        },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(TopBarComponent);
    fixture.detectChanges();
  }

  it('renders the real weather chip for the current garden', async () => {
    await setup(of({ temperatureC: 26.4, weatherCode: 1 }));
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('26°C');
    expect(text).toContain('partly cloudy');
  });

  it('hides the weather chip entirely when weather is unavailable', async () => {
    await setup(of(undefined));
    expect(fixture.nativeElement.querySelector('.weather')).toBeFalsy();
  });
});
