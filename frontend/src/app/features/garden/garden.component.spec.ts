import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { GardenComponent } from './garden.component';
import { GardenApi, MockGardenApi } from '../../core/services/garden.service';

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
});
