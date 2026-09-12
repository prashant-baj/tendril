import { ChangeDetectionStrategy, Component, EventEmitter, Output, signal } from '@angular/core';
import { IconComponent } from '../icon/icon.component';

/**
 * Photo capture-or-pick input, shared across every photo-involving story (OB-02 first; WS-05's
 * Capture screen reuses this exact component per docs/roadmap.md's resequencing note).
 *
 * Deliberately a plain `<input type="file" accept="image/*" capture>`, not `getUserMedia` +
 * a live camera preview: `getUserMedia` requires a secure context (HTTPS/localhost), and this
 * app is currently deployed over plain HTTP (ADR-0010) — the same class of bug already found
 * and fixed for `crypto.randomUUID()` (UserIdentityService). The `capture` attribute still
 * gets the native camera-or-gallery chooser on mobile without touching that API at all.
 */
@Component({
  selector: 'td-photo-picker',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './photo-picker.component.html',
  styleUrl: './photo-picker.component.scss',
})
export class PhotoPickerComponent {
  @Output() fileSelected = new EventEmitter<File | null>();

  readonly previewUrl = signal<string | null>(null);
  private readonly fileName = signal<string | null>(null);
  readonly selectedFileName = this.fileName.asReadonly();

  onFileChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    this.setFile(file);
  }

  clear(): void {
    this.setFile(null);
  }

  private setFile(file: File | null): void {
    const previous = this.previewUrl();
    if (previous) {
      URL.revokeObjectURL(previous);
    }
    this.previewUrl.set(file ? URL.createObjectURL(file) : null);
    this.fileName.set(file?.name ?? null);
    this.fileSelected.emit(file);
  }
}
