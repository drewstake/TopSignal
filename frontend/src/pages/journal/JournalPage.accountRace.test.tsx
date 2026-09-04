// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, RouterProvider, createMemoryRouter, useSearchParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { accountsApi, ApiError } from "../../lib/api";
import { ACCOUNT_QUERY_PARAM, parseAccountId } from "../../lib/accountSelection";
import type { AccountInfo, JournalEntry, JournalEntryImage } from "../../lib/types";
import { JournalPage } from "./JournalPage";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

function account(id: number, source: AccountInfo["trade_data_source"], isMain: boolean): AccountInfo {
  return {
    id,
    name: source === "csv_import" ? "Live Funded" : "Express Funded",
    provider_name: source === "csv_import" ? "Live Funded" : "Express Funded",
    custom_display_name: null,
    trade_data_source: source,
    balance: null,
    provider_data_stale: false,
    last_seen_at: null,
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: isMain,
    is_archived: false,
    can_trade: null,
    is_visible: true,
    last_trade_at: null,
  };
}

function entry(accountId: number, id: number, title: string, withStats = true): JournalEntry {
  return {
    id,
    account_id: accountId,
    entry_date: "2026-07-25",
    title,
    mood: "Neutral",
    tags: [],
    body: `${title} notes`,
    version: 1,
    stats_source: withStats ? "account_trades" : null,
    stats_json: withStats
      ? {
          snapshot_version: 1,
          trade_count: 1,
          total_pnl: 10,
          total_fees: 1,
          win_rate: 100,
          avg_win: 10,
          avg_loss: 0,
          largest_win: 10,
          largest_loss: 0,
          largest_position_size: 1,
          gross: 10,
          net: 9,
          net_realized_pnl: 9,
        }
      : null,
    stats_pulled_at: withStats ? "2026-07-25T15:00:00Z" : null,
    is_archived: false,
    created_at: "2026-07-25T15:00:00Z",
    updated_at: "2026-07-25T15:00:00Z",
  };
}

const express = account(7101, "projectx", true);
const live = account(88001, "csv_import", false);

function TestAppShellOutlet({
  accounts,
  accountsLoading = false,
  accountsError = null,
}: {
  accounts: AccountInfo[];
  accountsLoading?: boolean;
  accountsError?: string | null;
}) {
  const [searchParams] = useSearchParams();
  const queryAccountId = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));
  const selectedAccountId =
    accounts.find((candidate) => candidate.id === queryAccountId)?.id ??
    accounts.find((candidate) => candidate.is_main)?.id ??
    accounts[0]?.id ??
    null;
  return (
    <Outlet
      context={{
        accounts,
        accountsLoading,
        accountsError,
        selectedAccountId,
      }}
    />
  );
}

function createJournalRouter({
  accounts,
  accountsLoading = false,
  accountsError = null,
  initialEntry = "/?account=7101",
}: {
  accounts: AccountInfo[];
  accountsLoading?: boolean;
  accountsError?: string | null;
  initialEntry?: string;
}) {
  return createMemoryRouter(
    [
      {
        path: "/",
        element: (
          <TestAppShellOutlet
            accounts={accounts}
            accountsLoading={accountsLoading}
            accountsError={accountsError}
          />
        ),
        children: [{ index: true, element: <JournalPage /> }],
      },
    ],
    { initialEntries: [initialEntry] },
  );
}

function mountJournal({
  expressEntry = entry(express.id, 41, "Express Entry"),
  liveEntry = entry(live.id, 81, "Live Entry"),
  expressImages = [],
}: {
  expressEntry?: JournalEntry;
  liveEntry?: JournalEntry;
  expressImages?: JournalEntryImage[];
} = {}) {
  vi.spyOn(accountsApi, "getJournalEntries").mockImplementation(async (accountId) => ({
    items: [accountId === express.id ? expressEntry : liveEntry],
    total: 1,
  }));
  vi.spyOn(accountsApi, "listJournalImages").mockImplementation(async (accountId) =>
    accountId === express.id ? expressImages : [],
  );
  vi.spyOn(accountsApi, "pullJournalTradeStats").mockImplementation(async (accountId) =>
    accountId === express.id ? { ...expressEntry, stats_json: entry(express.id, 41, "Express Entry").stats_json } : liveEntry,
  );
  vi.spyOn(accountsApi, "getSummary").mockResolvedValue({} as never);
  vi.spyOn(accountsApi, "getTrades").mockResolvedValue([]);
  const router = createJournalRouter({ accounts: [express, live] });
  render(<RouterProvider router={router} />);
  return { router, expressEntry, liveEntry };
}

async function switchToLive(router: ReturnType<typeof createMemoryRouter>) {
  await act(async () => {
    await router.navigate("/?account=88001");
  });
  await screen.findByDisplayValue("Live Entry");
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("JournalPage account-switch mutation guards", () => {
  it("prevents Refresh from replacing an unsaved journal draft", async () => {
    const save = vi.spyOn(accountsApi, "updateJournalEntry").mockResolvedValue({
      ...entry(express.id, 41, "Unsaved draft"), version: 2,
    });
    mountJournal();
    const title = await screen.findByDisplayValue("Express Entry");
    fireEvent.change(title, { target: { value: "Unsaved draft" } });
    expect((screen.getByRole("button", { name: "Refresh" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByDisplayValue("Unsaved draft")).not.toBeNull();
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    await waitFor(() => expect((screen.getByRole("button", { name: "Refresh" }) as HTMLButtonElement).disabled).toBe(false));
  });
  it("reuses journal data on return visits and reloads entries with Refresh", async () => {
    const row = entry(97601, 400, "Cached journal entry");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => new Response(
      JSON.stringify(String(url).endsWith("/images") ? [] : { items: [row], total: 1 }),
      { status: 200 },
    ));
    const mount = () => render(<RouterProvider router={createJournalRouter({
      accounts: [account(row.account_id, "projectx", true)], initialEntry: "/?account=97601",
    })} />);
    const first = mount();
    await screen.findByDisplayValue(row.title);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    first.unmount();
    mount();
    await screen.findByDisplayValue(row.title);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await userEvent.setup().click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    expect(fetchMock.mock.calls.every(([, options]) => options?.method === "GET")).toBe(true);
  });
  it("keeps account and first entry loading behind a skeleton", async () => {
    const pendingEntries = deferred<{ items: JournalEntry[]; total: number }>();
    vi.spyOn(accountsApi, "getJournalEntries").mockReturnValue(pendingEntries.promise);
    const getAccounts = vi.spyOn(accountsApi, "getSelectableAccountsLocalFirst");
    const router = createJournalRouter({ accounts: [express] });

    render(<RouterProvider router={router} />);

    expect(screen.getByRole("status").textContent).toContain("Loading the active account and journal entries.");
    expect(screen.queryByText("No active account selected.")).toBeNull();
    expect(screen.queryByText("No entries match these filters.")).toBeNull();
    expect(getAccounts).not.toHaveBeenCalled();

    await act(async () => {
      pendingEntries.resolve({ items: [], total: 0 });
      await pendingEntries.promise;
    });

    expect(await screen.findByText("No entries match these filters.")).not.toBeNull();
    expect(screen.queryByText("Loading the active account and journal entries.")).toBeNull();
  });

  it("distinguishes shell loading, account errors, and a genuine no-account state", () => {
    const loadingRouter = createJournalRouter({ accounts: [], accountsLoading: true, initialEntry: "/" });
    const loadingRender = render(<RouterProvider router={loadingRouter} />);
    expect(screen.getByRole("status").textContent).toContain("Loading the active account and journal entries.");
    expect(screen.queryByText("No active account selected.")).toBeNull();
    loadingRender.unmount();

    const errorRouter = createJournalRouter({ accounts: [], accountsError: "Saved accounts unavailable", initialEntry: "/" });
    const errorRender = render(<RouterProvider router={errorRouter} />);
    expect(screen.getByRole("alert").textContent).toContain("Saved accounts unavailable");
    errorRender.unmount();

    const emptyRouter = createJournalRouter({ accounts: [], initialEntry: "/" });
    render(<RouterProvider router={emptyRouter} />);
    expect(screen.getByText("No active account selected.")).not.toBeNull();
  });

  it("requires a fresh entry scope when switching Express to Live and back to Express", async () => {
    const expressEntry = entry(express.id, 41, "Express Entry");
    const liveEntries = deferred<{ items: JournalEntry[]; total: number }>();
    const returningExpressEntries = deferred<{ items: JournalEntry[]; total: number }>();
    let expressEntryReads = 0;

    const getJournalEntries = vi.spyOn(accountsApi, "getJournalEntries").mockImplementation((accountId) => {
      if (accountId === express.id) {
        expressEntryReads += 1;
        return expressEntryReads === 1
          ? Promise.resolve({ items: [expressEntry], total: 1 })
          : returningExpressEntries.promise;
      }
      return liveEntries.promise;
    });
    vi.spyOn(accountsApi, "listJournalImages").mockResolvedValue([]);
    const router = createJournalRouter({ accounts: [express, live] });

    render(<RouterProvider router={router} />);
    expect(await screen.findByDisplayValue("Express Entry")).not.toBeNull();

    await act(async () => {
      await router.navigate("/?account=88001");
    });
    await waitFor(() => expect(getJournalEntries).toHaveBeenCalledWith(live.id, expect.any(Object), expect.any(Object)));

    await act(async () => {
      await router.navigate("/?account=7101");
    });
    await waitFor(() => expect(expressEntryReads).toBe(2));

    expect(screen.getByRole("status").textContent).toContain("Loading the active account and journal entries.");
    expect(screen.queryByText("No entries match these filters.")).toBeNull();

    await act(async () => {
      returningExpressEntries.resolve({ items: [expressEntry], total: 1 });
      await returningExpressEntries.promise;
    });

    expect(await screen.findByDisplayValue("Express Entry")).not.toBeNull();
  });

  it("ignores a stale AI skip result in the mounted production page", async () => {
    const user = userEvent.setup();
    const aiResult = deferred<Awaited<ReturnType<typeof accountsApi.generateAIJournalRecap>>>();
    vi.spyOn(accountsApi, "generateAIJournalRecap").mockReturnValue(aiResult.promise);
    const { router } = mountJournal();

    await screen.findByDisplayValue("Express Entry");
    await user.click(screen.getByRole("button", { name: "AI Recap" }));
    await waitFor(() => expect(accountsApi.generateAIJournalRecap).toHaveBeenCalledWith(7101, expect.any(Object)));
    await switchToLive(router);

    await act(async () => {
      aiResult.resolve({
        account_id: 7101,
        entry_date: "2026-07-25",
        journal_entry_id: null,
        created: false,
        updated: false,
        skipped: true,
        skip_reason: "no_trades_for_day",
        source_trade_count: 0,
        recap_markdown: "",
        generated_at: "2026-07-25T16:00:00Z",
      });
      await aiResult.promise;
    });

    expect(screen.queryByText(/No trades found/)).toBeNull();
    expect(screen.getByDisplayValue("Live Entry")).not.toBeNull();
    expect((screen.getByRole("button", { name: "AI Recap" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("does not insert an old-account create success into the new account", async () => {
    const user = userEvent.setup();
    const createdResult = deferred<Awaited<ReturnType<typeof accountsApi.createJournalEntry>>>();
    vi.spyOn(accountsApi, "createJournalEntry").mockReturnValue(createdResult.promise);
    const { router } = mountJournal();

    await screen.findByDisplayValue("Express Entry");
    await user.click(screen.getByRole("button", { name: "New Entry" }));
    await waitFor(() => expect(accountsApi.createJournalEntry).toHaveBeenCalledWith(7101, expect.any(Object)));
    await switchToLive(router);

    await act(async () => {
      createdResult.resolve({
        ...entry(7101, 42, "Old Account Created Entry"),
        already_existed: false,
      });
      await createdResult.promise;
    });

    expect(screen.queryByDisplayValue("Old Account Created Entry")).toBeNull();
    expect(screen.getByDisplayValue("Live Entry")).not.toBeNull();
    expect(screen.queryByText(/Failed to create/)).toBeNull();
  });

  it("does not expose an old autosave conflict or clear the new draft", async () => {
    const user = userEvent.setup();
    const updateResult = deferred<Awaited<ReturnType<typeof accountsApi.updateJournalEntry>>>();
    vi.spyOn(accountsApi, "updateJournalEntry").mockReturnValue(updateResult.promise);
    const { router, expressEntry } = mountJournal();

    const title = await screen.findByRole("textbox", { name: "Title" });
    await user.clear(title);
    await user.type(title, "Edited Express Entry");
    await waitFor(() => expect(accountsApi.updateJournalEntry).toHaveBeenCalled(), { timeout: 2_500 });
    await switchToLive(router);

    await act(async () => {
      updateResult.reject(
        new ApiError(
          "version_conflict",
          409,
          { detail: "version_conflict", server: { ...expressEntry, title: "Server Express Entry", version: 2 } },
          "version_conflict",
        ),
      );
      await updateResult.promise.catch(() => undefined);
    });

    expect(screen.queryByText("Reload server version")).toBeNull();
    expect(screen.getByDisplayValue("Live Entry")).not.toBeNull();
    expect(screen.queryByText("Save failed")).toBeNull();
  });

  it("does not show an old delete failure or change the new account busy state", async () => {
    const user = userEvent.setup();
    const deleteResult = deferred<void>();
    vi.spyOn(accountsApi, "deleteJournalEntry").mockReturnValue(deleteResult.promise);
    vi.stubGlobal("confirm", vi.fn(() => true));
    const { router } = mountJournal();

    await screen.findByDisplayValue("Express Entry");
    await user.click(screen.getByRole("button", { name: "Delete Entry" }));
    await waitFor(() => expect(accountsApi.deleteJournalEntry).toHaveBeenCalledWith(7101, 41));
    await switchToLive(router);

    await act(async () => {
      deleteResult.reject(new Error("Old account delete failed"));
      await deleteResult.promise.catch(() => undefined);
    });

    expect(screen.queryByText("Old account delete failed")).toBeNull();
    expect((screen.getByRole("button", { name: "Delete Entry" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByDisplayValue("Live Entry")).not.toBeNull();
  });

  it("does not restore an old image after a stale delete failure", async () => {
    const user = userEvent.setup();
    const deleteResult = deferred<void>();
    vi.spyOn(accountsApi, "deleteJournalImage").mockReturnValue(deleteResult.promise);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new Blob(["image"]), { status: 200 })));
    const image: JournalEntryImage = {
      id: 9,
      journal_entry_id: 41,
      account_id: 7101,
      entry_date: "2026-07-25",
      filename: "chart.png",
      mime_type: "image/png",
      byte_size: 5,
      width: 10,
      height: 10,
      created_at: "2026-07-25T15:00:00Z",
      url: "/api/accounts/7101/journal/41/images/9",
    };
    const { router } = mountJournal({ expressImages: [image] });

    const imageButton = await screen.findByRole("button", { name: /Journal image 1/ });
    imageButton.focus();
    await user.keyboard("{Delete}");
    await waitFor(() => expect(accountsApi.deleteJournalImage).toHaveBeenCalledWith(7101, 41, 9));
    await switchToLive(router);

    await act(async () => {
      deleteResult.reject(new Error("Old image delete failed"));
      await deleteResult.promise.catch(() => undefined);
    });

    expect(screen.queryByText("Old image delete failed")).toBeNull();
    expect(screen.queryByRole("button", { name: /Journal image 1/ })).toBeNull();
    expect(screen.getByDisplayValue("Live Entry")).not.toBeNull();
  });

  it("does not attach a completed old-account image upload to the new entry", async () => {
    const uploadResult = deferred<JournalEntryImage>();
    vi.spyOn(accountsApi, "uploadJournalImage").mockReturnValue(uploadResult.promise);
    const { router } = mountJournal();

    const notes = await screen.findByPlaceholderText(/What did the market do/);
    const file = new File(["image"], "chart.png", { type: "image/png" });
    fireEvent.paste(notes, {
      clipboardData: {
        items: [{ kind: "file", type: "image/png", getAsFile: () => file }],
      },
    });
    await waitFor(() => expect(accountsApi.uploadJournalImage).toHaveBeenCalledWith(7101, 41, file));
    await switchToLive(router);

    await act(async () => {
      uploadResult.resolve({
        id: 10,
        journal_entry_id: 41,
        account_id: 7101,
        entry_date: "2026-07-25",
        filename: "chart.png",
        mime_type: "image/png",
        byte_size: 5,
        width: 10,
        height: 10,
        created_at: "2026-07-25T15:00:00Z",
        url: "/api/accounts/7101/journal/41/images/10",
      });
      await uploadResult.promise;
    });

    expect(screen.queryByRole("button", { name: /Journal image 1/ })).toBeNull();
    expect(screen.getByDisplayValue("Live Entry")).not.toBeNull();
  });

  it("does not apply a stale trade-stat pull or complete a stale copy action", async () => {
    const user = userEvent.setup();
    const oldEntry = entry(7101, 41, "Express Entry", false);
    const statsResult = deferred<JournalEntry>();
    const summaryResult = deferred<never>();
    vi.spyOn(accountsApi, "pullJournalTradeStats").mockReturnValue(statsResult.promise);
    const { router } = mountJournal({ expressEntry: oldEntry });
    vi.mocked(accountsApi.getSummary).mockReturnValue(summaryResult.promise);

    await screen.findByDisplayValue("Express Entry");
    await user.click(screen.getByRole("button", { name: "Copy Entry" }));
    await waitFor(() => expect(accountsApi.pullJournalTradeStats).toHaveBeenCalledWith(7101, 41));
    await waitFor(() => expect(accountsApi.getSummary).toHaveBeenCalledWith(7101, expect.any(Object)));
    await switchToLive(router);

    await act(async () => {
      statsResult.resolve({ ...oldEntry, title: "Stats-mutated Express Entry", stats_json: entry(7101, 41, "x").stats_json });
      summaryResult.reject(new Error("Old copy stats failed"));
      await Promise.allSettled([statsResult.promise, summaryResult.promise]);
    });

    expect(screen.queryByDisplayValue("Stats-mutated Express Entry")).toBeNull();
    expect(screen.queryByText(/Copied current entry/)).toBeNull();
    expect(screen.queryByText(/Old copy stats failed/)).toBeNull();
    expect(screen.getByDisplayValue("Live Entry")).not.toBeNull();
  });
});
