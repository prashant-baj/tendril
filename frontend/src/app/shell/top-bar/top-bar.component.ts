import { DecimalPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  ElementRef,
  HostListener,
  inject,
  signal,
} from '@angular/core';
import { toObservable, toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter, map, of, startWith, switchMap } from 'rxjs';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { LayoutService } from '../../core/services/layout.service';
import { CurrentGardenService } from '../../core/services/current-garden.service';
import { GardenApi } from '../../core/services/garden.service';
import { GardenSummary } from '../../core/models/garden.model';
import { weatherPresentation } from '../../shared/components/weather-presentation.util';

/**
 * Sticky top bar — screen title (from the active route's `data.title`), garden switcher,
 * weather chip, and a notification bell that opens Activity. Matches the mockup's <header>.
 */
@Component({
  selector: 'td-top-bar',
  standalone: true,
  imports: [RouterLink, IconComponent, DecimalPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './top-bar.component.html',
  styleUrl: './top-bar.component.scss',
})
export class TopBarComponent {
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly gardenApi = inject(GardenApi);
  private readonly elementRef = inject(ElementRef<HTMLElement>);
  readonly layout = inject(LayoutService);
  private readonly currentGarden = inject(CurrentGardenService);

  readonly garden = toSignal(this.currentGarden.garden$, { initialValue: undefined });

  // Real weather for the currently-selected garden's location — refetched whenever the current
  // garden changes (garden switcher). `undefined` (no garden yet, or geocode/forecast failure)
  // hides the chip entirely rather than showing stale or broken data.
  readonly weather = toSignal(
    toObservable(this.currentGarden.gardenId).pipe(
      switchMap((gardenId) => (gardenId ? this.gardenApi.getGardenWeather(gardenId) : of(undefined))),
    ),
    { initialValue: undefined },
  );

  readonly weatherPresentation = computed(() => {
    const w = this.weather();
    return w ? weatherPresentation(w.weatherCode) : undefined;
  });

  // The switcher's own garden list is fetched lazily (only once the dropdown is first opened)
  // rather than eagerly on every screen — most sessions never touch it.
  readonly gardens = signal<GardenSummary[]>([]);
  readonly dropdownOpen = signal(false);

  readonly title = toSignal(
    this.router.events.pipe(
      filter((e) => e instanceof NavigationEnd),
      startWith(null),
      map(() => this.deepestTitle()),
    ),
    { initialValue: this.deepestTitle() },
  );

  private deepestTitle(): string {
    let r = this.route.snapshot.root;
    while (r.firstChild) r = r.firstChild;
    return (r.data['title'] as string) ?? '';
  }

  toggleGardenDropdown(): void {
    if (this.dropdownOpen()) {
      this.dropdownOpen.set(false);
      return;
    }
    this.dropdownOpen.set(true);
    this.gardenApi.getGardens().subscribe((gardens) => this.gardens.set(gardens));
  }

  selectGarden(gardenId: string): void {
    this.currentGarden.setCurrentGardenId(gardenId);
    this.dropdownOpen.set(false);
  }

  // Closes the dropdown on any click outside this component — a plain host-element containment
  // check, no CDK overlay needed for something this small.
  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    if (this.dropdownOpen() && !this.elementRef.nativeElement.contains(event.target as Node)) {
      this.dropdownOpen.set(false);
    }
  }
}
