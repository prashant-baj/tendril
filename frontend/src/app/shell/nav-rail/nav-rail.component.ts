import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter, map, startWith } from 'rxjs';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { NAV_ITEMS } from '../nav-items';
import { activeNavId } from '../active-nav.util';

/** Desktop (≥900px) left navigation rail — logo, nav items, capture CTA, user footer. */
@Component({
  selector: 'td-nav-rail',
  standalone: true,
  imports: [RouterLink, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './nav-rail.component.html',
  styleUrl: './nav-rail.component.scss',
})
export class NavRailComponent {
  private readonly router = inject(Router);
  readonly navItems = NAV_ITEMS;

  readonly activeId = toSignal(
    this.router.events.pipe(
      filter((e) => e instanceof NavigationEnd),
      startWith(null),
      map(() => activeNavId(this.router.url)),
    ),
    { initialValue: activeNavId(this.router.url) },
  );
}
