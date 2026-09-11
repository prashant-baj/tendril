import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivityComponent } from './activity.component';
import { ActivityApi, MockActivityApi } from '../../core/services/activity.service';

describe('ActivityComponent', () => {
  let fixture: ComponentFixture<ActivityComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ActivityComponent],
      providers: [{ provide: ActivityApi, useClass: MockActivityApi }],
    }).compileComponents();
    fixture = TestBed.createComponent(ActivityComponent);
    fixture.detectChanges();
  });

  it('renders the timeline including the quoted WhatsApp message', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Reminder sent on WhatsApp');
    expect(text).toContain('Good morning Meera');
  });
});
