import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { GardenSetupComponent } from './garden-setup.component';
import { GardenApi } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';

describe('GardenSetupComponent', () => {
  let fixture: ComponentFixture<GardenSetupComponent>;
  let component: GardenSetupComponent;
  let gardenApi: jasmine.SpyObj<GardenApi>;
  let currentGarden: jasmine.SpyObj<CurrentGardenService>;
  let router: Router;

  beforeEach(async () => {
    gardenApi = jasmine.createSpyObj<GardenApi>('GardenApi', ['createGarden']);
    currentGarden = jasmine.createSpyObj<CurrentGardenService>('CurrentGardenService', [
      'setCurrentGardenId',
    ]);

    await TestBed.configureTestingModule({
      imports: [GardenSetupComponent],
      providers: [
        provideRouter([]),
        { provide: GardenApi, useValue: gardenApi },
        { provide: CurrentGardenService, useValue: currentGarden },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(GardenSetupComponent);
    component = fixture.componentInstance;
    router = TestBed.inject(Router);
    fixture.detectChanges();
  });

  it('does not submit when required fields are missing', () => {
    component.submit();
    expect(gardenApi.createGarden).not.toHaveBeenCalled();
    expect(component.form.controls.name.touched).toBe(true);
    expect(component.form.controls.geolocation.touched).toBe(true);
  });

  it('creates the garden, sets it as current, and navigates to /home on success', () => {
    gardenApi.createGarden.and.returnValue(of({ gardenId: 'g-123' }));
    const navigateSpy = spyOn(router, 'navigateByUrl');

    component.form.setValue({ name: 'Balcony Garden', geolocation: 'Pune', vision: '' });
    component.submit();

    expect(gardenApi.createGarden).toHaveBeenCalledWith({
      name: 'Balcony Garden',
      geolocation: 'Pune',
      vision: undefined,
    });
    expect(currentGarden.setCurrentGardenId).toHaveBeenCalledWith('g-123');
    expect(navigateSpy).toHaveBeenCalledWith('/home');
  });

  it('shows a recoverable error and stays on the page when the create call fails', () => {
    gardenApi.createGarden.and.returnValue(throwError(() => new Error('network down')));
    const navigateSpy = spyOn(router, 'navigateByUrl');

    component.form.setValue({ name: 'Balcony Garden', geolocation: 'Pune', vision: '' });
    component.submit();

    expect(component.errorMessage()).toBeTruthy();
    expect(component.submitting()).toBe(false);
    expect(navigateSpy).not.toHaveBeenCalled();

    // recoverable: the form is still usable, and a retry can succeed.
    gardenApi.createGarden.and.returnValue(of({ gardenId: 'g-456' }));
    component.submit();
    expect(navigateSpy).toHaveBeenCalledWith('/home');
  });
});
