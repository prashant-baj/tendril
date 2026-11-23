import {
  AbstractControl,
  FormArray,
  FormControl,
  FormGroup,
  ValidationErrors,
  ValidatorFn,
  Validators,
} from '@angular/forms';
import { Garden, GardenContext } from '../../../core/models/garden.model';

/**
 * The Garden `context` block + climate zone as a reactive form (ADR-0032). Shared by Garden Setup
 * (optional section) and Garden settings (edit). Limits mirror the API's validation in
 * `app/api/garden_handler.py` so the user sees the problem before the request is sent.
 */
export const CONTEXT_TEXT_MAX = 1000;
export const CLIMATE_ZONE_MAX = 100;
export const RECIPIENT_NAME_MAX = 100;
export const MAX_RECIPIENTS = 10;
export const STAKE_NO_MAX = 99999;
export const E164_PATTERN = /^\+[1-9]\d{6,14}$/;

export const CONTEXT_TEXT_FIELDS = ['soil', 'irrigation', 'intercrop', 'layout', 'notes'] as const;
export type ContextTextField = (typeof CONTEXT_TEXT_FIELDS)[number];

export type RecipientGroup = FormGroup<{
  name: FormControl<string>;
  e164: FormControl<string>;
}>;

export type FarmDetailsGroup = FormGroup<{
  climateZone: FormControl<string>;
  soil: FormControl<string>;
  irrigation: FormControl<string>;
  intercrop: FormControl<string>;
  layout: FormControl<string>;
  notes: FormControl<string>;
  stakeMin: FormControl<number | null>;
  stakeMax: FormControl<number | null>;
  recipients: FormArray<RecipientGroup>;
}>;

const text = (max: number) => new FormControl('', { nonNullable: true, validators: [Validators.maxLength(max)] });
const stake = () =>
  new FormControl<number | null>(null, [
    Validators.min(1),
    Validators.max(STAKE_NO_MAX),
    Validators.pattern(/^\d+$/),
  ]);

/** Both stake bounds or neither, and min <= max. */
export const stakeRangeValidator: ValidatorFn = (group: AbstractControl): ValidationErrors | null => {
  const min = group.get('stakeMin')?.value as number | null;
  const max = group.get('stakeMax')?.value as number | null;
  const hasMin = min !== null && min !== undefined && `${min}` !== '';
  const hasMax = max !== null && max !== undefined && `${max}` !== '';
  if (hasMin !== hasMax) return { stakeRangeIncomplete: true };
  if (hasMin && hasMax && Number(min) > Number(max)) return { stakeRangeOrder: true };
  return null;
};

export function newRecipientGroup(value?: { name: string; e164: string }): RecipientGroup {
  return new FormGroup({
    name: new FormControl(value?.name ?? '', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(RECIPIENT_NAME_MAX)],
    }),
    e164: new FormControl(value?.e164 ?? '', {
      nonNullable: true,
      validators: [Validators.required, Validators.pattern(E164_PATTERN)],
    }),
  });
}

export function buildFarmDetailsGroup(): FarmDetailsGroup {
  return new FormGroup(
    {
      climateZone: text(CLIMATE_ZONE_MAX),
      soil: text(CONTEXT_TEXT_MAX),
      irrigation: text(CONTEXT_TEXT_MAX),
      intercrop: text(CONTEXT_TEXT_MAX),
      layout: text(CONTEXT_TEXT_MAX),
      notes: text(CONTEXT_TEXT_MAX),
      stakeMin: stake(),
      stakeMax: stake(),
      recipients: new FormArray<RecipientGroup>([], [Validators.maxLength(MAX_RECIPIENTS)]),
    },
    { validators: [stakeRangeValidator] },
  );
}

/** Fills the group from a stored garden (Garden settings). */
export function patchFarmDetails(group: FarmDetailsGroup, garden: Garden): void {
  const ctx = garden.context ?? {};
  group.patchValue({
    climateZone: garden.climateZone ?? '',
    soil: ctx.soil ?? '',
    irrigation: ctx.irrigation ?? '',
    intercrop: ctx.intercrop ?? '',
    layout: ctx.layout ?? '',
    notes: ctx.notes ?? '',
    stakeMin: ctx.stakeRange?.min ?? null,
    stakeMax: ctx.stakeRange?.max ?? null,
  });
  group.controls.recipients.clear();
  for (const r of ctx.notificationRecipients ?? []) {
    group.controls.recipients.push(newRecipientGroup(r));
  }
}

/**
 * The form value in API shape. Blank text fields and an empty recipient list are omitted, so an
 * untouched form yields `context: {}` and `climateZone: ''` — callers decide whether that means
 * "send nothing" (create) or "clear it" (update replaces the whole block).
 */
export function farmDetailsToRequest(group: FarmDetailsGroup): {
  climateZone: string;
  context: GardenContext;
} {
  const v = group.getRawValue();
  const context: GardenContext = {};
  for (const field of CONTEXT_TEXT_FIELDS) {
    const value = v[field].trim();
    if (value) context[field] = value;
  }
  if (v.stakeMin !== null && v.stakeMax !== null && `${v.stakeMin}` !== '' && `${v.stakeMax}` !== '') {
    context.stakeRange = { min: Number(v.stakeMin), max: Number(v.stakeMax) };
  }
  const recipients = v.recipients
    .map((r) => ({ name: r.name.trim(), e164: r.e164.trim() }))
    .filter((r) => r.name || r.e164);
  if (recipients.length) context.notificationRecipients = recipients;
  return { climateZone: v.climateZone.trim(), context };
}

export function isEmptyFarmDetails(value: { climateZone: string; context: GardenContext }): boolean {
  return !value.climateZone && Object.keys(value.context).length === 0;
}
