import { describe, expect, it } from "vitest";

import {
  describeAccountProviderSync,
  describeProviderRefreshException,
  formatAccountBalance,
  getAccountProviderState,
  getAvailableAccountBalance,
  reclassifyAccountListProviderFreshness,
  summarizeAccountProviderSync,
} from "./accountProviderState";
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
    provider_sync_status: "provider_fresh",
    provider_sync_error_code: null,
    provider_sync_error_message: null,
    provider_last_successful_refresh_at: "2026-07-29T14:00:00Z",
    last_seen_at: "2026-07-29T14:00:00Z",
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

describe("account provider state", () => {
  it("preserves a missing balance as unavailable rather than zero", () => {
    expect(getAvailableAccountBalance(null)).toBeNull();
    expect(getAvailableAccountBalance(0)).toBe(0);
    expect(formatAccountBalance(null)).toBe("Unavailable");
    expect(formatAccountBalance(0)).toBe("$0.00");
  });

  it("keeps provider staleness and last-seen metadata", () => {
    expect(
      getAccountProviderState({
        balance: null,
        provider_data_stale: true,
        last_seen_at: "2026-07-10T14:30:00.000Z",
      }),
    ).toEqual({
      balance: null,
      stale: true,
      lastSeenAt: "2026-07-10T14:30:00.000Z",
    });
  });

  it("surfaces the most severe provider state without treating Live CSV as stale", () => {
    const summary = summarizeAccountProviderSync([
      providerAccount({ id: 1, provider_sync_status: "cache_fresh" }),
      providerAccount({
        id: 2,
        provider_data_stale: true,
        provider_sync_status: "cached_fallback",
        provider_sync_error_code: "projectx_auth_failed",
        provider_sync_error_message: null,
      }),
      providerAccount({
        id: 88_001,
        trade_data_source: "csv_import",
        provider_data_stale: false,
        provider_sync_status: "not_applicable",
      }),
    ]);

    expect(summary?.status).toBe("cached_fallback");
    expect(describeAccountProviderSync(summary)).toMatchObject({
      tone: "error",
      message: expect.stringContaining("rejected the saved credential"),
    });
  });

  it("reports the timestamp from the row that determines the provider warning", () => {
    const summary = summarizeAccountProviderSync([
      providerAccount({
        id: 1,
        provider_sync_status: "provider_fresh",
        provider_last_successful_refresh_at: "2026-07-29T15:00:00Z",
        last_seen_at: "2026-07-29T15:00:00Z",
      }),
      providerAccount({
        id: 2,
        provider_data_stale: true,
        provider_sync_status: "cache_stale",
        provider_last_successful_refresh_at: "2026-07-20T12:00:00Z",
        last_seen_at: "2026-07-20T12:00:00Z",
      }),
    ]);

    expect(summary).toMatchObject({
      status: "cache_stale",
      lastSuccessfulRefreshAt: "2026-07-20T12:00:00Z",
    });
  });

  it("reclassifies saved provider rows at the server-defined stale deadline", () => {
    const deadline = "2026-07-29T15:00:00.000Z";
    const nowMs = Date.parse(deadline);

    const [providerFresh, cacheFresh, cachedFallback, csvImport] =
      reclassifyAccountListProviderFreshness([
        providerAccount({
          id: 1,
          provider_sync_status: "provider_fresh",
          provider_data_stale_at: deadline,
        }),
        providerAccount({
          id: 2,
          provider_sync_status: "cache_fresh",
          provider_data_stale_at: deadline,
        }),
        providerAccount({
          id: 3,
          provider_sync_status: "cached_fallback",
          provider_data_stale_at: deadline,
          provider_sync_error_code: "projectx_network_error",
          provider_sync_error_message: "ProjectX could not be reached.",
        }),
        providerAccount({
          id: 4,
          trade_data_source: "csv_import",
          provider_data_stale: true,
          provider_data_stale_at: deadline,
          provider_sync_status: "not_applicable",
        }),
      ], nowMs);

    expect(providerFresh).toMatchObject({
      provider_data_stale: true,
      provider_sync_status: "cache_stale",
    });
    expect(cacheFresh).toMatchObject({
      provider_data_stale: true,
      provider_sync_status: "cache_stale",
    });
    expect(cachedFallback).toMatchObject({
      provider_data_stale: true,
      provider_sync_status: "cached_fallback",
      provider_sync_error_code: "projectx_network_error",
      provider_sync_error_message: "ProjectX could not be reached.",
    });
    expect(csvImport).toMatchObject({
      provider_data_stale: false,
      provider_sync_status: "not_applicable",
    });
  });

  it("does not freshen a row the backend has already classified as stale", () => {
    const account = providerAccount({
      provider_data_stale: true,
      provider_sync_status: "cache_stale",
      provider_data_stale_at: "2026-07-29T15:00:00.000Z",
    });

    expect(
      reclassifyAccountListProviderFreshness(
        [account],
        Date.parse("2026-07-29T14:59:59.999Z"),
      )[0],
    ).toBe(account);
  });

  it("uses only structured safe messages for rejected refresh requests", () => {
    expect(describeProviderRefreshException({
      detail: {
        code: "projectx_credentials_not_configured",
        message: "Connect ProjectX for this signed-in user.",
      },
    })).toBe("Connect ProjectX for this signed-in user.");
    expect(describeProviderRefreshException(new Error("unexpected internal detail"))).toContain(
      "ProjectX account refresh failed",
    );
  });
});
