import { describe, expect, it } from "vitest";

import type { AccountInfo } from "../../lib/types";
import {
  resolveLiveAccountEnableDecision,
  resolveProjectXAccountId,
} from "./liveAccountMode";

function account(
  id: number,
  tradeDataSource: AccountInfo["trade_data_source"],
  isMain = false,
): AccountInfo {
  return {
    id,
    name: `Account ${id}`,
    provider_name: `Account ${id}`,
    custom_display_name: null,
    trade_data_source: tradeDataSource,
    balance: null,
    provider_data_stale: false,
    last_seen_at: null,
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: isMain,
    is_archived: false,
    can_trade: tradeDataSource === "projectx",
    is_visible: true,
    last_trade_at: null,
  };
}

describe("resolveLiveAccountEnableDecision", () => {
  it("selects the only separate Live CSV account", () => {
    expect(
      resolveLiveAccountEnableDecision([
        account(10, "projectx", true),
        account(20, "csv_import"),
      ]),
    ).toEqual({ kind: "select", accountId: 20 });
  });

  it("opens setup instead of converting an Express account when no Live account exists", () => {
    expect(resolveLiveAccountEnableDecision([account(10, "projectx", true)])).toEqual({
      kind: "setup",
      liveAccountCount: 0,
    });
  });

  it("opens a chooser instead of guessing when multiple Live accounts exist", () => {
    expect(
      resolveLiveAccountEnableDecision([
        account(10, "projectx", true),
        account(20, "csv_import"),
        account(30, "csv_import"),
      ]),
    ).toEqual({ kind: "setup", liveAccountCount: 2 });
  });
});

describe("resolveProjectXAccountId", () => {
  const accounts = [
    account(10, "projectx"),
    account(20, "projectx", true),
    account(30, "csv_import"),
  ];

  it("returns the previously selected ProjectX account when it remains available", () => {
    expect(resolveProjectXAccountId(accounts, 10)).toBe(10);
  });

  it("falls back to the main ProjectX account and never mutates the CSV account", () => {
    expect(resolveProjectXAccountId(accounts, null)).toBe(20);
  });

  it("returns null when no ProjectX account is available", () => {
    expect(resolveProjectXAccountId([account(30, "csv_import")], 10)).toBeNull();
  });
});
