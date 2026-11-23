import { ComponentFixture, TestBed } from '@angular/core/testing';
import { MAX_PHOTO_BYTES, PhotoPickerComponent } from './photo-picker.component';

describe('PhotoPickerComponent', () => {
  let fixture: ComponentFixture<PhotoPickerComponent>;
  let component: PhotoPickerComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [PhotoPickerComponent] }).compileComponents();
    fixture = TestBed.createComponent(PhotoPickerComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  function fileInputEvent(file: File): Event {
    const input = document.createElement('input');
    input.type = 'file';
    Object.defineProperty(input, 'files', { value: [file] });
    return { target: input } as unknown as Event;
  }

  it('starts with no preview (file-picker fallback shown)', () => {
    expect(component.previewUrl()).toBeNull();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Take or choose a photo');
  });

  it('emits the selected file and shows a preview', () => {
    const emitted: (File | null)[] = [];
    component.fileSelected.subscribe((f) => emitted.push(f));

    const file = new File(['x'], 'tomato.jpg', { type: 'image/jpeg' });
    component.onFileChange(fileInputEvent(file));
    fixture.detectChanges();

    expect(emitted).toEqual([file]);
    expect(component.previewUrl()).toBeTruthy();
    expect(component.selectedFileName()).toBe('tomato.jpg');
  });

  it('clear() removes the preview and emits null', () => {
    const file = new File(['x'], 'tomato.jpg', { type: 'image/jpeg' });
    component.onFileChange(fileInputEvent(file));

    const emitted: (File | null)[] = [];
    component.fileSelected.subscribe((f) => emitted.push(f));
    component.clear();

    expect(emitted).toEqual([null]);
    expect(component.previewUrl()).toBeNull();
  });

  it('refuses a photo over 10 MB with a clear message and emits null (ADR-0033)', () => {
    const big = new File(['x'], 'huge.jpg', { type: 'image/jpeg' });
    Object.defineProperty(big, 'size', { value: 11 * 1024 * 1024 });
    const emitted: (File | null)[] = [];
    component.fileSelected.subscribe((f) => emitted.push(f));

    component.onFileChange(fileInputEvent(big));
    fixture.detectChanges();

    expect(emitted).toEqual([null]);
    expect(component.previewUrl()).toBeNull();
    expect(component.error()).toContain('up to 10 MB');
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('11.0 MB');
  });

  it('accepts a photo of exactly 10 MB and clears a previous error', () => {
    const big = new File(['x'], 'huge.jpg', { type: 'image/jpeg' });
    Object.defineProperty(big, 'size', { value: 11 * 1024 * 1024 });
    component.onFileChange(fileInputEvent(big));
    const ok = new File(['x'], 'ok.jpg', { type: 'image/jpeg' });
    Object.defineProperty(ok, 'size', { value: MAX_PHOTO_BYTES });

    component.onFileChange(fileInputEvent(ok));

    expect(component.error()).toBeNull();
    expect(component.selectedFileName()).toBe('ok.jpg');
  });
});
