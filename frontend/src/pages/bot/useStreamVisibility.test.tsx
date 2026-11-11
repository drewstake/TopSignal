// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { STREAM_HIDDEN_GRACE_MS, useStreamVisibility } from "./useStreamVisibility";

let hidden = false;
beforeEach(() => {
  vi.useFakeTimers();
  hidden = false;
  vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

const setHidden = (value: boolean) => act(() => {
  hidden = value;
  document.dispatchEvent(new Event("visibilitychange"));
});

it("keeps streams enabled through a brief hide and disables them after the grace", () => {
  const { result } = renderHook(() => useStreamVisibility());
  expect(result.current).toBe(true);
  setHidden(true);
  act(() => { vi.advanceTimersByTime(STREAM_HIDDEN_GRACE_MS - 1); });
  expect(result.current).toBe(true);
  setHidden(false);
  setHidden(true);
  act(() => { vi.advanceTimersByTime(STREAM_HIDDEN_GRACE_MS - 1); });
  expect(result.current).toBe(true);  // The grace restarts after each visible period.
  act(() => { vi.advanceTimersByTime(1); });
  expect(result.current).toBe(false);
  setHidden(false);
  expect(result.current).toBe(true);
});

it("does not open streams for a page that loads hidden", () => {
  hidden = true;
  const { result } = renderHook(() => useStreamVisibility());
  expect(result.current).toBe(false);
});
