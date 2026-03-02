import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MAIN_ACCOUNT_STORAGE_KEY,
  MAIN_ACCOUNT_UPDATED_EVENT,
  readStoredMainAccountId,
  writeStoredMainAccountId,
  type MainAccountUpdatedDetail,
} from "./accountSelection";

describe("accountSelection main account persistence", () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    const eventTarget = new EventTarget();

    vi.stubGlobal("window", {
      addEventListener: eventTarget.addEventListener.bind(eventTarget),
      removeEventListener: eventTarget.removeEventListener.bind(eventTarget),
      dispatchEvent: eventTarget.dispatchEvent.bind(eventTarget),
      localStorage: {
        getItem: (key: string) => (values.has(key) ? values.get(key)! : null),
        setItem: (key: string, value: string) => {
          values.set(key, value);
        },
        removeItem: (key: string) => {
          values.delete(key);
        },
        clear: () => {
          values.clear();
        },
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("writes main account and emits a browser event", () => {
    const listener = vi.fn((event: Event) => {
      const detail = (event as CustomEvent<MainAccountUpdatedDetail>).detail;
      expect(detail).toEqual({ accountId: 7012 });
    });
    window.addEventListener(MAIN_ACCOUNT_UPDATED_EVENT, listener);

    writeStoredMainAccountId(7012);

    expect(window.localStorage.getItem(MAIN_ACCOUNT_STORAGE_KEY)).toBe("7012");
    expect(readStoredMainAccountId()).toBe(7012);
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
