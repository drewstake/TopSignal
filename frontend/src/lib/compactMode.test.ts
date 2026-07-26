// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { isCompactModeEnabled, setCompactModeEnabled, useCompactMode } from "./compactMode";

afterEach(() => {
  window.localStorage.clear();
});

describe("compactMode", () => {
  it("persists the selected dashboard view", () => {
    expect(isCompactModeEnabled()).toBe(false);

    setCompactModeEnabled(true);
    expect(isCompactModeEnabled()).toBe(true);
    expect(window.localStorage.getItem("topsignal.compactMode")).toBe("true");

    setCompactModeEnabled(false);
    expect(isCompactModeEnabled()).toBe(false);
  });

  it("keeps mounted consumers in sync", () => {
    const first = renderHook(() => useCompactMode());
    const second = renderHook(() => useCompactMode());

    act(() => first.result.current.setEnabled(true));

    expect(first.result.current.enabled).toBe(true);
    expect(second.result.current.enabled).toBe(true);
  });

  it("returns to standard mode when another tab clears browser preferences", () => {
    window.localStorage.setItem("topsignal.compactMode", "true");
    const consumer = renderHook(() => useCompactMode());

    expect(consumer.result.current.enabled).toBe(true);

    window.localStorage.clear();
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: null }));
    });

    expect(consumer.result.current.enabled).toBe(false);
  });
});
