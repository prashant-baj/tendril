/** Maps a raw Open-Meteo WMO `weatherCode` (kept UI-agnostic server-side, `GardenWeather`) to a
 * short label + Material icon — mirrors `specialist-icon.util.ts`/`activity-presentation.util.ts`'s
 * "keep the backend free of UI concerns" pattern. Only the codes Open-Meteo's `current` forecast
 * actually returns are covered; anything else falls back to a generic label/icon. */
export interface WeatherPresentation {
  label: string;
  icon: string;
}

export function weatherPresentation(code: number): WeatherPresentation {
  if (code === 0) return { label: 'clear', icon: 'sunny' };
  if (code <= 2) return { label: 'partly cloudy', icon: 'partly_cloudy_day' };
  if (code === 3) return { label: 'cloudy', icon: 'cloud' };
  if (code === 45 || code === 48) return { label: 'foggy', icon: 'foggy' };
  if (code >= 51 && code <= 67) return { label: 'rainy', icon: 'rainy' };
  if (code >= 71 && code <= 77) return { label: 'snowy', icon: 'weather_snowy' };
  if (code >= 80 && code <= 82) return { label: 'showers', icon: 'rainy' };
  if (code >= 95 && code <= 99) return { label: 'stormy', icon: 'thunderstorm' };
  return { label: 'mild', icon: 'partly_cloudy_day' };
}
