import {
  buildFarmDetailsGroup,
  farmDetailsToRequest,
  isEmptyFarmDetails,
  newRecipientGroup,
  patchFarmDetails,
} from './farm-details.util';

describe('farm-details util (ADR-0032)', () => {
  it('an untouched form is empty and valid', () => {
    const group = buildFarmDetailsGroup();
    expect(group.valid).toBeTrue();
    const value = farmDetailsToRequest(group);
    expect(value).toEqual({ climateZone: '', context: {} });
    expect(isEmptyFarmDetails(value)).toBeTrue();
  });

  it('trims text, drops blanks, and builds stakeRange + recipients', () => {
    const group = buildFarmDetailsGroup();
    group.patchValue({ climateZone: ' Semi-arid ', soil: ' clay ', layout: '   ', stakeMin: 1, stakeMax: 105 });
    group.controls.recipients.push(newRecipientGroup({ name: ' Anil ', e164: '+919800000002' }));
    expect(farmDetailsToRequest(group)).toEqual({
      climateZone: 'Semi-arid',
      context: {
        soil: 'clay',
        stakeRange: { min: 1, max: 105 },
        notificationRecipients: [{ name: 'Anil', e164: '+919800000002' }],
      },
    });
  });

  it('validates the stake range: both or neither, and in order', () => {
    const group = buildFarmDetailsGroup();
    group.patchValue({ stakeMin: 5 });
    expect(group.errors?.['stakeRangeIncomplete']).toBeTrue();
    group.patchValue({ stakeMax: 2 });
    expect(group.errors?.['stakeRangeOrder']).toBeTrue();
    group.patchValue({ stakeMax: 9 });
    expect(group.errors).toBeNull();
  });

  it('validates recipients: name required, E.164 number', () => {
    const r = newRecipientGroup({ name: '', e164: '9800000001' });
    expect(r.controls.name.valid).toBeFalse();
    expect(r.controls.e164.valid).toBeFalse();
    r.patchValue({ name: 'Owner', e164: '+919800000001' });
    expect(r.valid).toBeTrue();
  });

  it('patches from a garden and replaces existing recipient rows', () => {
    const group = buildFarmDetailsGroup();
    group.controls.recipients.push(newRecipientGroup({ name: 'Old', e164: '+919800000009' }));
    patchFarmDetails(group, {
      gardenId: 'g',
      name: 'Farm',
      geolocation: 'x',
      vision: '',
      climateZone: 'Semi-arid',
      context: { irrigation: 'drip', notificationRecipients: [{ name: 'Owner', e164: '+919800000001' }] },
    });
    expect(group.controls.irrigation.value).toBe('drip');
    expect(group.controls.recipients.length).toBe(1);
    expect(group.controls.recipients.at(0).controls.name.value).toBe('Owner');
  });
});
