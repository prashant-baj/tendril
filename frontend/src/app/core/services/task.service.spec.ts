import { MockTaskApi } from './task.service';

describe('MockTaskApi', () => {
  let api: MockTaskApi;

  beforeEach(() => {
    api = new MockTaskApi();
  });

  it('starts with t1 pre-completed, matching the mockup fixture', () => {
    const t1 = api.todayTasks().find((t) => t.taskId === 't1');
    expect(t1?.done).toBe(true);
    expect(t1?.chip).toBe('Done');
  });

  it('toggling a task flips it everywhere it appears (today list + grouped list)', () => {
    api.toggle('t2');
    expect(api.todayTasks().find((t) => t.taskId === 't2')?.done).toBe(true);

    const today = api.taskGroups().find((g) => g.title === 'Today');
    expect(today?.items.find((t) => t.taskId === 't2')?.done).toBe(true);

    api.toggle('t2');
    expect(api.todayTasks().find((t) => t.taskId === 't2')?.done).toBe(false);
  });

  it('replaces the chip label with "Done" only while completed', () => {
    const before = api.todayTasks().find((t) => t.taskId === 't2')!;
    expect(before.chip).toBe('Today');
    api.toggle('t2');
    expect(api.todayTasks().find((t) => t.taskId === 't2')?.chip).toBe('Done');
  });
});
