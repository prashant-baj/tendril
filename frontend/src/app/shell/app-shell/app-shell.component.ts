import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { LayoutService } from '../../core/services/layout.service';
import { TopBarComponent } from '../top-bar/top-bar.component';
import { NavRailComponent } from '../nav-rail/nav-rail.component';
import { BottomNavComponent } from '../bottom-nav/bottom-nav.component';

/**
 * Root app layout — desktop nav rail (≥900px) or mobile top bar + bottom nav (<900px)
 * around a scrollable content area. Matches the mockup's outer device/app-column structure.
 */
@Component({
  selector: 'td-app-shell',
  standalone: true,
  imports: [RouterOutlet, TopBarComponent, NavRailComponent, BottomNavComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './app-shell.component.html',
  styleUrl: './app-shell.component.scss',
})
export class AppShellComponent {
  readonly layout = inject(LayoutService);
}
