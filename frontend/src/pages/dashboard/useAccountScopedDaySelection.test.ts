// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useAccountScopedDaySelection } from "./useAccountScopedDaySelection";

describe("useAccountScopedDaySelection", () => {
  it("does not resurrect account A's day after an A to B to A round trip", () => {
    const { result, rerender } = renderHook(
      ({ accountId }) => useAccountScopedDaySelection(accountId),
      { initialProps: { accountId: 1011 as number | null } },
    );

    act(() => result.current.setSelectedDate("2026-07-22"));
    expect(result.current.selectedDate).toBe("2026-07-22");

    rerender({ accountId: 2022 });
    expect(result.current.selectedDate).toBeNull();
    rerender({ accountId: 1011 });
    expect(result.current.selectedDate).toBeNull();
  });
});
