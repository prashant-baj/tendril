import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { AppShellComponent } from './app-shell.component';
import { GardenApi, MockGardenApi } from '../../core/services/garden.service';

describe('AppShellComponent', () => {
  let fixture: ComponentFixture<AppShellComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AppShellComponent],
      providers: [provideRouter([]), { provide: GardenApi, useClass: MockGardenApi }],
    }).compileComponents();
    fixture = TestBed.createComponent(AppShellComponent);
    fixture.detectChanges();
  });

  it('creates and shows the mobile bottom nav by default (narrow test viewport)', () => {
    expect(fixture.componentInstance).toBeTruthy();
    expect((fixture.nativeElement as HTMLElement).querySelector('td-bottom-nav')).toBeTruthy();
  });
});
