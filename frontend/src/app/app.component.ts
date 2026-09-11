import { Component } from '@angular/core';
import { AppShellComponent } from './shell/app-shell/app-shell.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [AppShellComponent],
  template: `<td-app-shell />`,
})
export class AppComponent {}
