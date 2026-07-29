// @vitest-environment jsdom

import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAccountProviderFreshness } from "./accountProviderState";
import type { AccountInfo } from "./types";

function providerAccount(overrides: Partial<AccountInfo> = {}): AccountInfo {
  return {
    id: 7301,
    name: "Express",
    provider_name: "Express",
    custom_display_name: null,
    trade_data_source: "projectx",
    balance: 50_000,
    provider_data_stale: false,
    provider_data_stale_at: "2026-07-29T15:00:01.000Z",
    provider_sync_status: "provider_fresh",
    provider_sync_error_code: null,
    provider_sync_error_message: null,
    provider_last_successful_refresh_at: "2026-07-29T14:45:01.000Z",
    last_seen_at: "2026-07-29T14:45:01.000Z",
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: true,
    is_archived: false,
    can_trade: true,
    is_visible: true,
    last_trade_at: null,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useAccountProviderFreshness", () => {
  it("marks an open-page provider snapshot stale at its deadline without a request", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-29T15:00:00.000Z"));
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const account = providerAccount();

    const { result } = renderHook(() => useAccountProviderFreshness([account]));

    expect(result.current[0]).toMatchObject({
      provider_data_stale: false,
      provider_sync_status: "provider_fresh",
    });

    act(() => vi.advanceTimersByTime(999));
    expect(result.current[0]?.provider_data_stale).toBe(false);

    act(() => vi.advanceTimersByTime(1));
    expect(result.current[0]).toMatchObject({
      provider_data_stale: true,
      provider_sync_status: "cache_stale",
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("uses the nearest future deadline and preserves cached fallback status", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-29T15:00:00.000Z"));
    const first = providerAccount({
      id: 1,
      provider_data_stale_at: "2026-07-29T15:00:02.000Z",
      provider_sync_status: "cached_fallback",
    });
    const second = providerAccount({
      id: 2,
      provider_data_stale_at: "2026-07-29T15:00:05.000Z",
      provider_sync_status: "cache_fresh",
    });

    const { result } = renderHook(() => useAccountProviderFreshness([second, first]));
    act(() => vi.advanceTimersByTime(2_000));

    expect(result.current[1]).toMatchObject({
      provider_data_stale: true,
      provider_sync_status: "cached_fallback",
    });
    expect(result.current[0]).toMatchObject({
      provider_data_stale: false,
      provider_sync_status: "cache_fresh",
    });
  });
});
