import { ComponentFixture, TestBed } from '@angular/core/testing';
import { PlantRowComponent } from './plant-row.component';
import { Plant } from '../../../core/models/plant.model';

describe('PlantRowComponent', () => {
  let fixture: ComponentFixture<PlantRowComponent>;
  let component: PlantRowComponent;

  const basePlant: Plant = {
    plantId: 'p-1',
    name: 'Curry Leaves',
    species: 'Curry Leaves',
    variety: '',
    stage: 'new',
    icon: 'eco',
    healthState: 'healthy',
    meta: '',
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PlantRowComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(PlantRowComponent);
    component = fixture.componentInstance;
  });

  it('shows the generic icon when the plant has no photoUrl', () => {
    component.plant = basePlant;
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('img.photo')).toBeFalsy();
    expect(el.querySelector('td-icon')).toBeTruthy();
  });

  it('shows the real photo when photoUrl is present', () => {
    component.plant = { ...basePlant, photoUrl: 'https://example.test/p-1.jpg' };
    fixture.detectChanges();

    const img = (fixture.nativeElement as HTMLElement).querySelector(
      'img.photo',
    ) as HTMLImageElement;
    expect(img).toBeTruthy();
    expect(img.src).toBe('https://example.test/p-1.jpg');
  });

  it('falls back to the icon if the photo fails to load', () => {
    component.plant = { ...basePlant, photoUrl: 'https://example.test/broken.jpg' };
    fixture.detectChanges();

    component.onPhotoError();
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('img.photo')).toBeFalsy();
    expect(el.querySelector('td-icon')).toBeTruthy();
  });
});
