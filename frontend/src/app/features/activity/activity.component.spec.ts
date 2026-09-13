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

  it('renders without error when there is no activity yet', () => {
    expect(fixture.nativeElement).toBeTruthy();
  });
});
