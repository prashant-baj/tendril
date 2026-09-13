import { MockTaskApi } from './task.service';

describe('MockTaskApi', () => {
  let api: MockTaskApi;

  beforeEach(() => {
    api = new MockTaskApi();
  });

  it('starts blank (no task backend yet)', () => {
    expect(api.todayTasks()).toEqual([]);
    expect(api.taskGroups()).toEqual([
      { title: 'Today', items: [] },
      { title: 'This week', items: [] },
      { title: 'Later', items: [] },
    ]);
  });

  it('toggle() is a no-op when the task id does not exist', () => {
    expect(() => api.toggle('nonexistent')).not.toThrow();
    expect(api.todayTasks()).toEqual([]);
  });
});
