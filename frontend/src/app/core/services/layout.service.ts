import { Injectable, signal } from '@angular/core';
import { BreakpointObserver } from '@angular/cdk/layout';

/**
 * Wide (desktop rail) vs. narrow (mobile bottom nav) shell layout, driven by the same
 * 900px breakpoint the mockup used (there via a ResizeObserver on document.body; here via
 * the CDK's BreakpointObserver, which also plays nicely with SSR/testing).
 */
@Injectable({ providedIn: 'root' })
export class LayoutService {
  private readonly wideBreakpoint = `(min-width: 900px)`;
  private readonly _isWide = signal(false);
  readonly isWide = this._isWide.asReadonly();

  constructor(private readonly breakpointObserver: BreakpointObserver) {
    this._isWide.set(this.breakpointObserver.isMatched(this.wideBreakpoint));
    this.breakpointObserver.observe(this.wideBreakpoint).subscribe((state) => {
      this._isWide.set(state.matches);
    });
  }
}
