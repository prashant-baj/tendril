/** Mirrors the GARDEN entity in docs/architecture/architecture.md §2. */
export interface Garden {
  gardenId: string;
  name: string;
  vision: string;
  geolocation: string;
  climateZone: string;
}

/** A small labelled fact chip (sun hours, container type, etc.). */
export interface GardenFact {
  icon: string;
  label: string;
}
