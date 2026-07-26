// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { accountsApi } from "../../lib/api";
import { DashboardPage } from "./DashboardPage";

function renderDemoDashboard(compact: boolean) {
  window.localStorage.setItem("topsignal.demoMode", "true");
  window.localStorage.setItem("topsignal.compactMode", compact ? "true" : "false");
  const router = createMemoryRouter(
    [
      { path: "/dashboard", element: <DashboardPage /> },
      { path: "/journal", element: <div>Demo journal destination</div> },
    ],
    { initialEntries: ["/dashboard?account=910001"] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe("Dashboard Demo journal isolation", () => {
  beforeEach(() => {
    window.localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("opens an existing standard-calendar journal fixture without creating cloud data", async () => {
    const createJournalEntry = vi.spyOn(accountsApi, "createJournalEntry");
    const router = renderDemoDashboard(false);

    fireEvent.click(
      await screen.findByRole("button", { name: /Jul 24, 2026\..*Net P&L/i }, { timeout: 10_000 }),
    );
    fireEvent.click(await screen.findByRole("button", { name: /^Open Journal Entry$/i }, { timeout: 10_000 }));

    expect(await screen.findByText("Demo journal destination")).toBeTruthy();
    expect(router.state.location.search).toBe("?account=910001&date=2026-07-24");
    expect(createJournalEntry).not.toHaveBeenCalled();
  }, 20_000);

  it("opens a Compact calendar day read-only without creating cloud data", async () => {
    const createJournalEntry = vi.spyOn(accountsApi, "createJournalEntry");
    const getJournalDays = vi
      .spyOn(accountsApi, "getJournalDays")
      .mockResolvedValue({ days: ["2026-07-24"] });
    const router = renderDemoDashboard(true);

    const compactDashboard = await screen.findByRole("region", { name: "Compact dashboard" }, { timeout: 10_000 });
    await waitFor(
      () =>
        expect(getJournalDays).toHaveBeenCalledWith(910001, {
          start_date: "2026-07-01",
          end_date: "2026-07-31",
        }),
      { timeout: 10_000 },
    );
    fireEvent.click(
      await within(compactDashboard).findByRole(
        "button",
        { name: /July 24, 2026,.*journal entry/i },
        { timeout: 10_000 },
      ),
    );
    fireEvent.click(
      await within(compactDashboard).findByRole(
        "button",
        { name: "Open journal for Jul 24" },
        { timeout: 10_000 },
      ),
    );

    await waitFor(() => expect(screen.getByText("Demo journal destination")).toBeTruthy());
    expect(router.state.location.search).toBe("?account=910001&date=2026-07-24");
    expect(getJournalDays).toHaveBeenCalled();
    expect(createJournalEntry).not.toHaveBeenCalled();
  }, 20_000);
});
