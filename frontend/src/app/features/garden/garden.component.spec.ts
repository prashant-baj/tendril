import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { GardenComponent } from './garden.component';
import { GardenApi, MockGardenApi } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';

describe('GardenComponent', () => {
  let fixture: ComponentFixture<GardenComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GardenComponent],
      providers: [provideRouter([]), { provide: GardenApi, useClass: MockGardenApi }],
    }).compileComponents();
    fixture = TestBed.createComponent(GardenComponent);
    fixture.detectChanges();
  });

  it('renders the garden name and its plant rows', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Balcony Kitchen Garden');
    expect(text).toContain('Tomato #2');
    expect(text).toContain('Mint box');
  });

  it('renders the plain card (no hero banner) when the garden has no photo', () => {
    const intro = (fixture.nativeElement as HTMLElement).querySelector('.intro');
    expect(intro?.classList.contains('has-photo')).toBe(false);
  });
});

describe('GardenComponent with a garden photo', () => {
  let fixture: ComponentFixture<GardenComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GardenComponent],
      providers: [
        provideRouter([]),
        { provide: GardenApi, useClass: MockGardenApi },
        {
          provide: CurrentGardenService,
          useValue: {
            gardenId: () => 'g-1',
            garden$: of({
              gardenId: 'g-1',
              name: 'Photo Garden',
              vision: '',
              geolocation: 'Pune',
              climateZone: '',
              photoUrl: 'https://example.com/banner.jpg',
            }),
          },
        },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(GardenComponent);
    fixture.detectChanges();
  });

  it('renders a photo-backed hero banner when the garden has a photoUrl', () => {
    const intro = (fixture.nativeElement as HTMLElement).querySelector('.intro');
    expect(intro?.classList.contains('has-photo')).toBe(true);
    expect((intro as HTMLElement).style.backgroundImage).toContain(
      'https://example.com/banner.jpg',
    );
  });
});
