import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter, map, startWith } from 'rxjs';
import { IconComponent } from '../../shared/components/icon/icon.component';
import { LayoutService } from '../../core/services/layout.service';

/**
 * Sticky top bar — screen title (from the active route's `data.title`), garden switcher,
 * weather chip, and a notification bell that opens Activity. Matches the mockup's <header>.
 */
@Component({
  selector: 'td-top-bar',
  standalone: true,
  imports: [RouterLink, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './top-bar.component.html',
  styleUrl: './top-bar.component.scss',
})
export class TopBarComponent {
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  readonly layout = inject(LayoutService);

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
}
