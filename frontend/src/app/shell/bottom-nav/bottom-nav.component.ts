import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter, map, startWith } from 'rxjs';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { NAV_ITEMS } from '../nav-items';
import { activeNavId } from '../active-nav.util';

/** Mobile (<900px) bottom tab bar with a floating capture CTA — matches the mockup's <nav>. */
@Component({
  selector: 'td-bottom-nav',
  standalone: true,
  imports: [RouterLink, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './bottom-nav.component.html',
  styleUrl: './bottom-nav.component.scss',
})
export class BottomNavComponent {
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
