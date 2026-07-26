// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DEMO_MODE_STORAGE_KEY, setDemoModeEnabled, useDemoMode } from "./demoMode";

describe("useDemoMode cross-tab synchronization", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setDemoModeEnabled(false);
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it("applies native storage changes and identifies their source", () => {
    const { result } = renderHook(() => useDemoMode());
    expect(result.current.enabled).toBe(false);

    act(() => {
      window.localStorage.setItem(DEMO_MODE_STORAGE_KEY, "true");
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: DEMO_MODE_STORAGE_KEY,
          newValue: "true",
          storageArea: window.localStorage,
        }),
      );
    });

    expect(result.current.enabled).toBe(true);
    expect(result.current.changeSource).toBe("storage");
  });

  it("keeps same-tab changes distinguishable from cross-tab changes", () => {
    const { result } = renderHook(() => useDemoMode());

    act(() => result.current.setEnabled(true));

    expect(result.current.enabled).toBe(true);
    expect(result.current.changeSource).toBe("local");
  });
});
