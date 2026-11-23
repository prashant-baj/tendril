import { ChangeDetectionStrategy, Component, EventEmitter, Output, signal } from '@angular/core';
import { IconComponent } from '../icon/icon.component';

/** ADR-0033: the upload limit, mirrored from the Client API's `sizeBytes` check. */
export const MAX_PHOTO_BYTES = 10 * 1024 * 1024;

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
  /** Shown instead of a preview when a picked file is refused (e.g. over 10 MB). */
  readonly error = signal<string | null>(null);

  onFileChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    // ADR-0033: photos are limited to 10 MB. Refuse here, before any upload, with a clear message
    // (the server also rejects it). Reset the input so picking the same file again re-triggers.
    if (file && file.size > MAX_PHOTO_BYTES) {
      input.value = '';
      this.setFile(null);
      this.error.set(`That photo is ${(file.size / (1024 * 1024)).toFixed(1)} MB. Photos can be up to 10 MB.`);
      return;
    }
    this.error.set(null);
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
