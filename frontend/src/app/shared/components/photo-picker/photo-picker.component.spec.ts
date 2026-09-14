import { ComponentFixture, TestBed } from '@angular/core/testing';
import { PhotoPickerComponent } from './photo-picker.component';

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
});
