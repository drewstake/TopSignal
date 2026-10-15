// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { FundedRuntime, initialView, type View } from './runtime';

class TestWorker {
  static latest: TestWorker;
  onmessage?: (event: MessageEvent<View>) => void;
  postMessage = vi.fn();
  terminate = vi.fn();
  constructor() { TestWorker.latest = this; }
  publish(view: View) { this.onmessage?.({ data: view } as MessageEvent<View>); }
}
beforeEach(() => { localStorage.clear(); vi.stubGlobal('Worker', TestWorker); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('delivers every immutable publication while persisting only seed changes', () => {
  const save = vi.spyOn(Storage.prototype, 'setItem');
  const runtime = new FundedRuntime(), listener = vi.fn();
  runtime.subscribe(listener);
  const views = [initialView(73521), { ...initialView(73521), step: 1 }, { ...initialView(73521), step: 2 }, initialView(73522)];
  for (const view of views) { TestWorker.latest.publish(view); expect(runtime.getSnapshot()).toBe(view); }
  expect(listener).toHaveBeenCalledTimes(4);
  expect(save.mock.calls).toEqual([['topsignal.get-funded.v3.seed', '73521'], ['topsignal.get-funded.v3.seed', '73522']]);
  runtime.dispatch({ type: 'play', playing: false });
  expect(TestWorker.latest.postMessage).toHaveBeenLastCalledWith({ type: 'play', playing: false });
  runtime.dispose(); expect(TestWorker.latest.terminate).toHaveBeenCalledOnce();
});

it('keeps publication delivery working after storage failure and retries persistence', () => {
  const save = vi.spyOn(Storage.prototype, 'setItem').mockImplementationOnce(() => { throw new Error('Storage unavailable'); });
  const runtime = new FundedRuntime(), listener = vi.fn(); runtime.subscribe(listener);
  for (let step = 0; step < 3; step++) TestWorker.latest.publish({ ...initialView(73521), step });
  expect(listener).toHaveBeenCalledTimes(3); expect(runtime.getSnapshot().step).toBe(2);
  expect(save).toHaveBeenCalledTimes(2); expect(localStorage.getItem('topsignal.get-funded.v3.seed')).toBe('73521');
  runtime.dispose();
});
