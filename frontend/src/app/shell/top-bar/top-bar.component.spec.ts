import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Observable, of } from 'rxjs';
import { TopBarComponent } from './top-bar.component';
import { GardenApi, MockGardenApi } from '../../core/services/garden.service';
import { Garden } from '../../core/models/garden.model';

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
