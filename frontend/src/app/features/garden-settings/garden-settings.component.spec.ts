import { HttpErrorResponse } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { GardenSettingsComponent } from './garden-settings.component';
import { GardenApi } from '../../core/services/garden.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import { Garden } from '../../core/models/garden.model';

const GARDEN: Garden = {
  gardenId: 'g-1',
  name: 'Bramhangaon Farm',
  geolocation: 'Kopargaon',
  vision: '105 healthy palms',
  climateZone: 'Semi-arid',
  context: {
    soil: 'Heavy black clay',
    stakeRange: { min: 1, max: 105 },
    notificationRecipients: [{ name: 'Owner', e164: '+919800000001' }],
  },
};

describe('GardenSettingsComponent', () => {
  let fixture: ComponentFixture<GardenSettingsComponent>;
  let component: GardenSettingsComponent;
  let gardenApi: jasmine.SpyObj<GardenApi>;
  let router: Router;

  async function setup(gardenId: string | null, load = of(GARDEN)) {
    gardenApi = jasmine.createSpyObj<GardenApi>('GardenApi', ['getGardenById', 'updateGarden']);
    gardenApi.getGardenById.and.returnValue(load);
    await TestBed.configureTestingModule({
      imports: [GardenSettingsComponent],
      providers: [
        provideRouter([]),
        { provide: GardenApi, useValue: gardenApi },
        { provide: CurrentGardenService, useValue: { gardenId: () => gardenId } },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(GardenSettingsComponent);
    component = fixture.componentInstance;
    router = TestBed.inject(Router);
    fixture.detectChanges();
  }

  it('fills the form from the stored garden', async () => {
    await setup('g-1');
    const farm = component.form.controls.farm;
    expect(component.form.controls.vision.value).toBe('105 healthy palms');
    expect(farm.controls.climateZone.value).toBe('Semi-arid');
    expect(farm.controls.soil.value).toBe('Heavy black clay');
    expect(farm.controls.stakeMin.value).toBe(1);
    expect(farm.controls.recipients.length).toBe(1);
    expect(component.gardenName()).toBe('Bramhangaon Farm');
  });

  it('saves the whole block (PATCH replaces it) and returns to the garden', async () => {
    await setup('g-1');
    gardenApi.updateGarden.and.returnValue(of(GARDEN));
    const navigateSpy = spyOn(router, 'navigateByUrl');

    component.form.controls.farm.controls.soil.setValue(''); // clearing a field clears it
    component.save();

    expect(gardenApi.updateGarden).toHaveBeenCalledWith('g-1', {
      vision: '105 healthy palms',
      climateZone: 'Semi-arid',
      context: {
        stakeRange: { min: 1, max: 105 },
        notificationRecipients: [{ name: 'Owner', e164: '+919800000001' }],
      },
    });
    expect(navigateSpy).toHaveBeenCalledWith('/garden');
  });

  it('does not save an invalid phone number', async () => {
    await setup('g-1');
    component.form.controls.farm.controls.recipients.at(0).controls.e164.setValue('98000');
    component.save();
    expect(gardenApi.updateGarden).not.toHaveBeenCalled();
  });

  it('never shows an editable form after a failed load, so it cannot wipe stored facts', async () => {
    await setup('g-1', throwError(() => new Error('offline')));
    expect(component.loadFailed()).toBe(true);
    expect((fixture.nativeElement as HTMLElement).querySelector('form')).toBeNull();
    component.save();
    expect(gardenApi.updateGarden).not.toHaveBeenCalled();
  });

  it('shows the API message when the server rejects the change', async () => {
    await setup('g-1');
    gardenApi.updateGarden.and.returnValue(
      throwError(() => new HttpErrorResponse({ status: 400, error: { message: 'context.soil must be a string' } })),
    );
    component.save();
    expect(component.errorMessage()).toBe("Couldn't save: context.soil must be a string");
    expect(component.saving()).toBe(false);
  });

  it('asks to set up a garden first when there is none', async () => {
    await setup(null);
    expect(component.noGarden()).toBe(true);
    expect(gardenApi.getGardenById).not.toHaveBeenCalled();
  });
});
