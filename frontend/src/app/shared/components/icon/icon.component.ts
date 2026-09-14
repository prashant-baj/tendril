import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

/**
 * A Material Symbols Rounded glyph — the mockup's `.ms`/`.msf` span pattern as a real
 * component instead of a raw `<span class="ms">`.
 */
@Component({
  selector: 'td-icon',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="ms" [class.msf]="filled" [style.font-size]="size">{{ name }}</span>`,
  styles: [':host { display: inline-flex; }'],
})
export class IconComponent {
  @Input({ required: true }) name!: string;
  /** Use the filled variant (the mockup's `.msf` class) for active/emphasized icons. */
  @Input() filled = false;
  @Input() size = '20px';
}
