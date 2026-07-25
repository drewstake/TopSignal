import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../../lib/api";
import type { AccountInfo, TradeImportPreview } from "../../../lib/types";
import { TradeImportPanel, TradeImportReview } from "./TradeImportPanel";
import { getTradeImportErrorMessage } from "./tradeImportErrors";

const preview: TradeImportPreview = {
  source_file_name: "trades_export.csv",
  file_sha256: "preview-sha",
  total_rows: 3,
  new_rows: 2,
  duplicate_rows: 1,
  summary: {
    gross_pnl: 334.5,
    fees: 2.96,
    commissions: 2,
    net_pnl: 329.54,
    wins: 2,
    losses: 0,
    breakeven: 1,
  },
  trades: [
    {
      row_number: 2,
      source_trade_id: "2815118967",
      contract_name: "MNQU6",
      symbol: "MNQ",
      entered_at: "2026-07-02T14:10:08Z",
      exited_at: "2026-07-02T14:10:48Z",
      entry_price: 30182.5,
      exit_price: 30148.75,
      fees: 2.22,
      commissions: 1.5,
      gross_pnl: 202.5,
      net_pnl: 198.78,
      size: 3,
      direction: "Short",
      trade_day: "2026-07-02",
      duration: "00:00:39.6715820",
      status: "new",
    },
    {
      row_number: 3,
      source_trade_id: "2815266522",
      contract_name: "MNQU6",
      symbol: "MNQ",
      entered_at: "2026-07-02T14:11:40Z",
      exited_at: "2026-07-02T14:18:09Z",
      entry_price: 30180.25,
      exit_price: 30114.25,
      fees: 0.74,
      commissions: 0.5,
      gross_pnl: 132,
      net_pnl: 130.76,
      size: 1,
      direction: "Short",
      trade_day: "2026-07-02",
      duration: "00:06:28.4929250",
      status: "duplicate",
    },
  ],
};

function liveAccount(id: number, name: string): AccountInfo {
  return {
    id,
    name,
    provider_name: name,
    custom_display_name: null,
    trade_data_source: "csv_import",
    balance: null,
    provider_data_stale: false,
    last_seen_at: null,
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: false,
    can_trade: null,
    is_visible: true,
    last_trade_at: null,
  };
}

describe("TradeImportReview", () => {
  it("renders totals and marks new and duplicate rows before confirmation", () => {
    const markup = renderToStaticMarkup(
      <TradeImportReview preview={preview} confirming={false} onConfirm={() => undefined} onCancel={() => undefined} />,
    );

    expect(markup).toContain("trades_export.csv");
    expect(markup).toContain("2 new");
    expect(markup).toContain("1 duplicate");
    expect(markup).toContain("2815118967");
    expect(markup).toContain("2815266522");
    expect(markup).toContain("Confirm Import (2)");
    expect(markup).toContain("Commissions");
    expect(markup).toContain("+$329.54");
  });

  it("disables confirmation when every row is already stored", () => {
    const duplicateOnly = {
      ...preview,
      new_rows: 0,
      duplicate_rows: preview.total_rows,
      trades: preview.trades.map((trade) => ({ ...trade, status: "duplicate" as const })),
    };
    const markup = renderToStaticMarkup(
      <TradeImportReview
        preview={duplicateOnly}
        confirming={false}
        onConfirm={() => undefined}
        onCancel={() => undefined}
      />,
    );

    expect(markup).toContain("Every parsed trade is already stored");
    expect(markup).toContain("Confirm Import (0)");
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>Confirm Import \(0\)<\/button>/);
  });

  it("renders only one review page for large parsed files", () => {
    const trades = Array.from({ length: 101 }, (_, index) => ({
      ...preview.trades[0],
      row_number: index + 2,
      source_trade_id: index === 100 ? "LAST-ROW-ID" : `trade-${index + 1}`,
    }));
    const markup = renderToStaticMarkup(
      <TradeImportReview
        preview={{
          ...preview,
          total_rows: trades.length,
          new_rows: trades.length,
          duplicate_rows: 0,
          trades,
        }}
        confirming={false}
        onConfirm={() => undefined}
        onCancel={() => undefined}
      />,
    );

    expect(markup).toContain("Rows 1–100 of 101");
    expect(markup).toContain("Next Rows");
    expect(markup).not.toContain("LAST-ROW-ID");
  });
});

describe("TradeImportPanel", () => {
  it("offers manual Live-account onboarding when ProjectX has no account to select", () => {
    const markup = renderToStaticMarkup(<TradeImportPanel accountId={null} />);

    expect(markup).toContain("Topstep Live account ID");
    expect(markup).toContain("Add Import Account");
    expect(markup).toContain("stay separate from your Express account");
    expect(markup).toContain("Add or select the Live account");
  });

  it("requires a separate Live CSV account before accepting a trade export", () => {
    const markup = renderToStaticMarkup(
      <TradeImportPanel accountId={88001} tradeDataSource="projectx" />,
    );

    expect(markup).toContain("Select or add a separate Live CSV account");
    expect(markup).toMatch(/<input[^>]*type="file"[^>]*disabled=""/);
  });

  it("accepts a trade export for a Live CSV account", () => {
    const markup = renderToStaticMarkup(
      <TradeImportPanel accountId={88001} tradeDataSource="csv_import" />,
    );

    expect(markup).not.toContain("Select or add a separate Live CSV account");
    expect(markup).not.toMatch(/<input[^>]*type="file"[^>]*disabled=""/);
  });

  it("offers existing Live CSV accounts in the separate account setup flow", () => {
    const markup = renderToStaticMarkup(
      <TradeImportPanel
        accountId={7301}
        tradeDataSource="projectx"
        liveAccounts={[
          liveAccount(88001, "Live Funded A"),
          liveAccount(88002, "Live Funded B"),
        ]}
      />,
    );

    expect(markup).not.toContain("Existing Live CSV accounts");

    const openMarkup = renderToStaticMarkup(
      <TradeImportPanel
        accountId={null}
        liveAccounts={[
          liveAccount(88001, "Live Funded A"),
          liveAccount(88002, "Live Funded B"),
        ]}
      />,
    );
    expect(openMarkup).toContain("Existing Live CSV accounts");
    expect(openMarkup).toContain("Live Funded A");
    expect(openMarkup).toContain("Live Funded B");
  });
});

describe("getTradeImportErrorMessage", () => {
  it("explains when the account is not in Live CSV mode", () => {
    const error = new ApiError(
      "conflict",
      409,
      null,
      "trade_import_requires_csv_import_account",
    );

    expect(getTradeImportErrorMessage(error)).toBe(
      "Select a separate Live CSV account before importing trades. Express accounts cannot be converted.",
    );
  });

  it("explains that a ProjectX account ID cannot be reused for Live CSV", () => {
    const error = new ApiError(
      "conflict",
      409,
      null,
      {
        code: "account_trade_data_source_conflict",
        current_trade_data_source: "projectx",
        requested_trade_data_source: "csv_import",
      },
    );

    expect(getTradeImportErrorMessage(error)).toBe(
      "That account ID already belongs to an Express/ProjectX account. Enter the separate Topstep Live account ID.",
    );
  });

  it("turns a structured missing-column response into a clear validation message", () => {
    const error = new ApiError(
      "missing columns",
      422,
      null,
      { code: "missing_columns", missing_columns: ["ExitedAt", "PnL"] },
    );

    expect(getTradeImportErrorMessage(error)).toBe("Missing required columns: ExitedAt, PnL.");
  });
});
