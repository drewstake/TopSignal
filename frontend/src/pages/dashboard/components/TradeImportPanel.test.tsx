// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { accountsApi, ApiError } from "../../../lib/api";
import { requestTradeImportFilePicker } from "../../../lib/tradeImportEvents";
import type { AccountInfo, TradeImportConfirmResult, TradeImportPreview, TradeImportStatus } from "../../../lib/types";
import { TradeImportPanel, TradeImportReview } from "./TradeImportPanel";
import { getTradeImportErrorMessage } from "./tradeImportErrors";
import { openTradeImportFilePicker } from "./tradeImportPicker";

const preview: TradeImportPreview = {
  preview_token: "opaque-preview-token",
  expires_at: "2026-07-02T15:00:00Z",
  source_file_name: "trades_export.csv",
  file_sha256: "preview-sha",
  total_rows: 3,
  new_rows: 2,
  duplicate_rows: 1,
  conflict_rows: 0,
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

const confirmResult: TradeImportConfirmResult = {
  import_id: 17,
  source_file_name: "trades_export.csv",
  imported_at: "2026-07-02T15:02:00Z",
  total_rows: 3,
  inserted_rows: 2,
  duplicate_rows: 1,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

function getNativeFileInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) {
    throw new Error("native file input not rendered");
  }
  return input;
}

function chooseFile(input: HTMLInputElement, file = new File(["Id,PnL\n1,100"], "trades_export.csv", { type: "text/csv" })) {
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
  vi.restoreAllMocks();
});

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
    is_archived: false,
    can_trade: null,
    is_visible: true,
    last_trade_at: null,
  };
}

describe("TradeImportReview", () => {
  it("renders totals and marks new and duplicate rows before confirmation", () => {
    const markup = renderToStaticMarkup(
      <TradeImportReview
        preview={preview}
        confirming={false}
        onConfirm={() => undefined}
        onChooseAnother={() => undefined}
        onClose={() => undefined}
      />,
    );

    expect(markup).toContain("trades_export.csv");
    expect(markup).toContain("2 new");
    expect(markup).toContain("1 duplicate");
    expect(markup).toContain("2815118967");
    expect(markup).toContain("2815266522");
    expect(markup).toContain("Confirm Import (2)");
    expect(markup).toContain("Choose Another File");
    expect(markup).toContain("Close");
    expect(markup).toContain("Commissions");
    expect(markup).toContain("+$329.54");
  });

  it("blocks confirmation and shows stored/incoming differences for identity conflicts", () => {
    const conflictPreview: TradeImportPreview = {
      ...preview,
      new_rows: 1,
      duplicate_rows: 1,
      conflict_rows: 1,
      total_rows: 3,
      trades: [
        ...preview.trades,
        {
          ...preview.trades[0],
          row_number: 4,
          status: "conflict",
          conflict: {
            identity_kind: "source_trade_id",
            identity_value: "2815118967",
            reason: "stored_trade_mismatch",
            stored_event_id: 99,
            differences: [
              { field: "net_pnl", stored: "198.78", incoming: "201.25" },
            ],
          },
        },
      ],
    };

    const markup = renderToStaticMarkup(
      <TradeImportReview
        preview={conflictPreview}
        confirming={false}
        onConfirm={() => undefined}
        onChooseAnother={() => undefined}
        onClose={() => undefined}
      />,
    );

    expect(markup).toContain("Resolve 1 conflicting trade before importing");
    expect(markup).toContain("Stored: 198.78");
    expect(markup).toContain("Incoming: 201.25");
    expect(markup).toContain("Nothing will be overwritten");
    expect(markup).not.toContain("Confirm Import");
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
        onChooseAnother={() => undefined}
        onClose={() => undefined}
      />,
    );

    expect(markup).toContain("Duplicate file");
    expect(markup).toContain("3 duplicates");
    expect(markup).toContain("All 3 trades are already imported. Nothing new was added.");
    expect(markup).toContain("Choose Another File");
    expect(markup).toContain("Close");
    expect(markup).not.toContain("Confirm Import");
    expect(markup).not.toContain("Review parsed trades");
    expect(markup).not.toContain("2815118967");
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
        onChooseAnother={() => undefined}
        onClose={() => undefined}
      />,
    );

    expect(markup).toContain("Rows 1–100 of 101");
    expect(markup).toContain("Next Rows");
    expect(markup).not.toContain("LAST-ROW-ID");
  });
});

describe("TradeImportPanel", () => {
  it("does not flash Live-account onboarding while saved accounts are loading", () => {
    const markup = renderToStaticMarkup(
      <TradeImportPanel accountId={null} accountsLoading liveAccounts={[]} />,
    );

    expect(markup).toContain("Loading account...");
    expect(markup).not.toContain("Topstep Live account ID");
    expect(markup).not.toContain("Account name");
    expect(markup).not.toContain("Add Import Account");
    expect(markup).not.toContain("Add or select the Live account before choosing a trade file.");
    expect(markup).not.toContain("Add Live Account");
  });

  it("offers manual Live-account onboarding when ProjectX has no account to select", () => {
    const markup = renderToStaticMarkup(<TradeImportPanel accountId={null} />);

    expect(markup).not.toContain("Topstep Live account ID");
    expect(markup).toContain("Account name");
    expect(markup).toContain("Starting balance");
    expect(markup).toContain("TOPX8141");
    expect(markup).toContain("Add Import Account");
    expect(markup).toContain("adds imported net P&amp;L to that balance automatically");
    expect(markup).toContain("Add or select the Live account");
    expect(markup).toContain('aria-expanded="true"');
    expect(markup).toMatch(/aria-controls="[^"]+-account-setup"/);
  });

  it("requires a separate Live CSV account before accepting a trade export", () => {
    const markup = renderToStaticMarkup(
      <TradeImportPanel accountId={88001} tradeDataSource="projectx" />,
    );

    expect(markup).toContain("Select or add a separate Live CSV account");
    expect(markup).toMatch(/<input[^>]*type="file"[^>]*disabled=""/);
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>Upload trade file<\/button>/);
  });

  it("accepts a trade export for a Live CSV account", () => {
    const markup = renderToStaticMarkup(
      <TradeImportPanel accountId={88001} tradeDataSource="csv_import" />,
    );

    expect(markup).not.toContain("Select or add a separate Live CSV account");
    expect(markup).not.toMatch(/<input[^>]*type="file"[^>]*disabled=""/);
    expect(markup).not.toContain("Import trades");
    expect(markup).not.toContain("Upload trade file");
    expect(markup).not.toContain("Accepted: .csv and .xlsx");
    expect(markup).not.toContain("No file selected");
    expect(markup).not.toContain("Topstep Live account ID");
    expect(markup).not.toContain("Add Live Account");
    expect(markup).not.toContain('aria-expanded="false"');
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
    expect(markup).toContain("Select Live Account");
    expect(markup).not.toContain("Add Live Account");

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
    expect(openMarkup).not.toContain("ID 88001");
    expect(openMarkup).not.toContain("ID 88002");
    expect(openMarkup).not.toContain("Topstep Live account ID");
    expect(openMarkup).not.toContain("Add Import Account");
  });

  it("offers keyboard-accessible Live-account creation only before an account exists", async () => {
    const user = userEvent.setup();
    render(<TradeImportPanel accountId={7301} tradeDataSource="projectx" />);

    const disclosure = screen.getByRole("button", { name: "Add Live Account" });
    disclosure.focus();
    await user.keyboard("{Enter}");
    expect(disclosure.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Account name")).not.toBeNull();
    expect(screen.queryByText("Topstep Live account ID")).toBeNull();
    await user.keyboard(" ");
    expect(disclosure.getAttribute("aria-expanded")).toBe("false");
  });

  it("creates a Live account from its name without asking for or sending an account ID", async () => {
    const user = userEvent.setup();
    const created = liveAccount(8_000_000_000_000_000, "TOPX8141");
    const create = vi.spyOn(accountsApi, "createLiveImportAccount").mockResolvedValue(created);
    const onAccountCreated = vi.fn();
    render(<TradeImportPanel accountId={null} onAccountCreated={onAccountCreated} />);

    await user.type(screen.getByLabelText("Account name"), "TOPX8141");
    await user.click(screen.getByRole("button", { name: "Add Import Account" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({ name: "TOPX8141", starting_balance: 10000 }),
    );
    expect(onAccountCreated).toHaveBeenCalledWith(created);
    expect(screen.queryByText("Topstep Live account ID")).toBeNull();
  });

  it("delegates to the native picker and clears its value for same-file reselection", () => {
    const click = vi.fn();
    const input = { value: "C:\\fakepath\\trades_export.csv", click };

    expect(openTradeImportFilePicker(input)).toBe(true);
    expect(input.value).toBe("");
    expect(click).toHaveBeenCalledTimes(1);

    input.value = "C:\\fakepath\\trades_export.csv";
    expect(openTradeImportFilePicker(input)).toBe(true);
    expect(input.value).toBe("");
    expect(click).toHaveBeenCalledTimes(2);
  });

  it("reports an unavailable native picker without throwing", () => {
    expect(openTradeImportFilePicker(null)).toBe(false);
  });

  it("delegates header and review requests with same-file reselection", async () => {
    const user = userEvent.setup();
    vi.spyOn(accountsApi, "previewTradeImport").mockResolvedValue(preview);
    render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);
    const input = getNativeFileInput();
    const nativeClick = vi.spyOn(input, "click").mockImplementation(() => undefined);
    act(() => requestTradeImportFilePicker());
    act(() => requestTradeImportFilePicker());
    expect(nativeClick).toHaveBeenCalledTimes(2);

    const file = chooseFile(input);
    await screen.findByText("Review parsed trades");
    await user.click(screen.getByRole("button", { name: "Choose Another File" }));
    expect(nativeClick).toHaveBeenCalledTimes(3);

    chooseFile(input, file);
    await waitFor(() => expect(accountsApi.previewTradeImport).toHaveBeenCalledTimes(2));
  });

  it("opens the same native picker from the Live account header action", () => {
    render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);
    const input = getNativeFileInput();
    const nativeClick = vi.spyOn(input, "click").mockImplementation(() => undefined);

    act(() => requestTradeImportFilePicker());

    expect(nativeClick).toHaveBeenCalledTimes(1);
  });

  it("cancels an in-flight preview without leaving a stale loading notice", async () => {
    const user = userEvent.setup();
    const pendingPreview = deferred<TradeImportPreview>();
    let requestSignal: AbortSignal | undefined;
    vi.spyOn(accountsApi, "previewTradeImport").mockImplementation((_accountId, _file, options) => {
      requestSignal = options?.signal;
      return pendingPreview.promise;
    });
    render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);

    chooseFile(getNativeFileInput());
    await screen.findByRole("button", { name: "Cancel" });
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(requestSignal?.aborted).toBe(true);
    expect(screen.queryByText(/Validating and parsing/)).toBeNull();
    expect(screen.queryByText("Import trades")).toBeNull();
    expect(getNativeFileInput().disabled).toBe(false);
  });

  it("offers Close and a valid Retry after a preview error", async () => {
    const user = userEvent.setup();
    vi.spyOn(accountsApi, "previewTradeImport")
      .mockRejectedValueOnce(new Error("Malformed export"))
      .mockRejectedValueOnce(new Error("Malformed export"))
      .mockResolvedValueOnce(preview);
    render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);
    const input = getNativeFileInput();

    chooseFile(input);
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("alert")).toBeNull();

    chooseFile(input);
    await screen.findByRole("button", { name: "Retry" });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Review parsed trades");
    expect(accountsApi.previewTradeImport).toHaveBeenCalledTimes(3);
  });

  it("dismisses success and can immediately import another file", async () => {
    const user = userEvent.setup();
    vi.spyOn(accountsApi, "previewTradeImport").mockResolvedValue(preview);
    vi.spyOn(accountsApi, "confirmTradeImport").mockResolvedValue(confirmResult);
    const firstRender = render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);

    chooseFile(getNativeFileInput());
    await screen.findByRole("button", { name: "Confirm Import (2)" });
    await user.click(screen.getByRole("button", { name: "Confirm Import (2)" }));
    await screen.findByText(/Imported 2 trades/);
    expect(accountsApi.confirmTradeImport).toHaveBeenCalledWith(
      88001,
      preview.preview_token,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText(/Imported 2 trades/)).toBeNull();

    firstRender.unmount();
    render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);
    const input = getNativeFileInput();
    const nativeClick = vi.spyOn(input, "click").mockImplementation(() => undefined);
    chooseFile(input);
    await screen.findByRole("button", { name: "Confirm Import (2)" });
    await user.click(screen.getByRole("button", { name: "Confirm Import (2)" }));
    await screen.findByRole("button", { name: "Import Another File" });
    await user.click(screen.getByRole("button", { name: "Import Another File" }));
    expect(nativeClick).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/Imported 2 trades/)).toBeNull();
  });

  it("recovers a committed result when the confirmation transport outcome is unknown", async () => {
    const user = userEvent.setup();
    const pendingStatus = deferred<TradeImportStatus>();
    vi.spyOn(accountsApi, "previewTradeImport").mockResolvedValue(preview);
    vi.spyOn(accountsApi, "confirmTradeImport").mockRejectedValue(new TypeError("Failed to fetch"));
    vi.spyOn(accountsApi, "getTradeImportStatus").mockReturnValue(pendingStatus.promise);
    render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);

    chooseFile(getNativeFileInput());
    await screen.findByRole("button", { name: "Confirm Import (2)" });
    await user.click(screen.getByRole("button", { name: "Confirm Import (2)" }));
    await screen.findByText("Import outcome unknown—checking status...");

    pendingStatus.resolve({
      status: "committed",
      confirmation_retryable: false,
      outcome_code: "committed",
      source_file_name: confirmResult.source_file_name,
      created_at: "2026-07-02T15:00:00Z",
      expires_at: "2026-07-02T15:30:00Z",
      confirmed_at: confirmResult.imported_at,
      total_rows: confirmResult.total_rows,
      new_rows: confirmResult.inserted_rows,
      duplicate_rows: confirmResult.duplicate_rows,
      conflict_rows: 0,
      result: confirmResult,
    });

    await screen.findByText(/Imported 2 trades/);
    expect(accountsApi.getTradeImportStatus).toHaveBeenCalledWith(
      88001,
      preview.preview_token,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("coalesces repeated confirmation clicks into one request", async () => {
    const user = userEvent.setup();
    const pendingConfirmation = deferred<TradeImportConfirmResult>();
    vi.spyOn(accountsApi, "previewTradeImport").mockResolvedValue(preview);
    vi.spyOn(accountsApi, "confirmTradeImport").mockReturnValue(pendingConfirmation.promise);
    render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);

    chooseFile(getNativeFileInput());
    const confirmButton = await screen.findByRole("button", { name: "Confirm Import (2)" });
    await user.dblClick(confirmButton);
    expect(accountsApi.confirmTradeImport).toHaveBeenCalledTimes(1);

    pendingConfirmation.resolve(confirmResult);
    await screen.findByText(/Imported 2 trades/);
  });

  it("recovers a pending confirmation after the panel is remounted", async () => {
    const user = userEvent.setup();
    const pendingConfirmation = deferred<TradeImportConfirmResult>();
    vi.spyOn(accountsApi, "previewTradeImport").mockResolvedValue(preview);
    vi.spyOn(accountsApi, "confirmTradeImport").mockReturnValue(pendingConfirmation.promise);
    const firstMount = render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);

    chooseFile(getNativeFileInput());
    await user.click(await screen.findByRole("button", { name: "Confirm Import (2)" }));
    await waitFor(() => expect(accountsApi.confirmTradeImport).toHaveBeenCalledTimes(1));
    firstMount.unmount();
    pendingConfirmation.resolve(confirmResult);
    await Promise.resolve();

    vi.spyOn(accountsApi, "getTradeImportStatus").mockResolvedValue({
      status: "committed",
      confirmation_retryable: false,
      outcome_code: "committed",
      source_file_name: confirmResult.source_file_name,
      created_at: "2026-07-02T15:00:00Z",
      expires_at: "2026-07-02T15:30:00Z",
      confirmed_at: confirmResult.imported_at,
      total_rows: confirmResult.total_rows,
      new_rows: confirmResult.inserted_rows,
      duplicate_rows: confirmResult.duplicate_rows,
      conflict_rows: 0,
      result: confirmResult,
    });
    render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);

    await screen.findByText(/Imported 2 trades/);
    expect(accountsApi.getTradeImportStatus).toHaveBeenCalledWith(
      88001,
      preview.preview_token,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("retries confirmation once when durable status says the prior attempt rolled back", async () => {
    const user = userEvent.setup();
    vi.spyOn(accountsApi, "previewTradeImport").mockResolvedValue(preview);
    vi.spyOn(accountsApi, "confirmTradeImport")
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(confirmResult);
    vi.spyOn(accountsApi, "getTradeImportStatus").mockResolvedValue({
      status: "pending",
      confirmation_retryable: true,
      outcome_code: "confirmation_retryable",
      source_file_name: confirmResult.source_file_name,
      created_at: "2026-07-02T15:00:00Z",
      expires_at: "2026-07-02T15:30:00Z",
      confirmed_at: null,
      total_rows: confirmResult.total_rows,
      new_rows: confirmResult.inserted_rows,
      duplicate_rows: confirmResult.duplicate_rows,
      conflict_rows: 0,
      result: null,
    });
    render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);

    chooseFile(getNativeFileInput());
    await user.click(await screen.findByRole("button", { name: "Confirm Import (2)" }));

    await screen.findByText(/Imported 2 trades/);
    expect(accountsApi.getTradeImportStatus).toHaveBeenCalledTimes(1);
    expect(accountsApi.confirmTradeImport).toHaveBeenCalledTimes(2);
  });

  it("lets the user leave a pending confirmation safely and checks the durable outcome", async () => {
    const user = userEvent.setup();
    const confirmation = deferred<TradeImportConfirmResult>();
    vi.spyOn(accountsApi, "previewTradeImport").mockResolvedValue(preview);
    vi.spyOn(accountsApi, "confirmTradeImport").mockReturnValue(confirmation.promise);
    vi.spyOn(accountsApi, "getTradeImportStatus").mockResolvedValue({
      status: "committed",
      confirmation_retryable: false,
      outcome_code: "committed",
      source_file_name: confirmResult.source_file_name,
      created_at: "2026-07-02T15:00:00Z",
      expires_at: "2026-07-02T15:30:00Z",
      confirmed_at: confirmResult.imported_at,
      total_rows: confirmResult.total_rows,
      new_rows: confirmResult.inserted_rows,
      duplicate_rows: confirmResult.duplicate_rows,
      conflict_rows: 0,
      result: confirmResult,
    });
    render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);

    chooseFile(getNativeFileInput());
    await user.click(await screen.findByRole("button", { name: "Confirm Import (2)" }));
    const firstSignal = vi.mocked(accountsApi.confirmTradeImport).mock.calls[0][2]?.signal;
    await user.click(await screen.findByRole("button", { name: "Cancel & Check Outcome" }));

    await screen.findByText(/Imported 2 trades/);
    expect(firstSignal?.aborted).toBe(false);
    expect(accountsApi.getTradeImportStatus).toHaveBeenCalledTimes(1);
  });

  it("supports Close and Choose Another on a partial-duplicate preview", async () => {
    const user = userEvent.setup();
    vi.spyOn(accountsApi, "previewTradeImport").mockResolvedValue(preview);
    render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);

    chooseFile(getNativeFileInput());
    await screen.findByText("Review parsed trades");
    expect(screen.getByText("1 duplicate")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Choose Another File" })).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText("Review parsed trades")).toBeNull();
  });

  it("keeps the fully duplicate state compact without offering another Live account", async () => {
    const user = userEvent.setup();
    const duplicateOnly: TradeImportPreview = {
      ...preview,
      new_rows: 0,
      duplicate_rows: preview.total_rows,
      conflict_rows: 0,
      trades: preview.trades.map((trade) => ({ ...trade, status: "duplicate" })),
    };
    vi.spyOn(accountsApi, "previewTradeImport").mockResolvedValue(duplicateOnly);
    render(<TradeImportPanel accountId={88001} tradeDataSource="csv_import" />);

    expect(screen.queryByRole("button", { name: "Add Live Account" })).toBeNull();

    chooseFile(getNativeFileInput());
    await screen.findByText("Duplicate file");
    expect(screen.queryByText("Review parsed trades")).toBeNull();
    expect(screen.queryByRole("button", { name: /Confirm Import/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Choose Another File" })).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText("Duplicate file")).toBeNull();
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

  it("explains a retryable name-only Live account creation conflict", () => {
    const error = new ApiError(
      "conflict",
      409,
      null,
      {
        code: "live_account_create_conflict_retryable",
      },
    );

    expect(getTradeImportErrorMessage(error)).toBe(
      "Another Live account change completed at the same time. Reload accounts and retry.",
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
