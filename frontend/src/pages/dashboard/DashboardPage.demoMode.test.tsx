// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { accountsApi } from "../../lib/api";
import { DashboardPage } from "./DashboardPage";

function renderDemoDashboard(compact: boolean) {
  window.localStorage.setItem("topsignal.demoMode", "true");
  window.localStorage.setItem("topsignal.compactMode", compact ? "true" : "false");
  const router = createMemoryRouter(
    [{ path: "/dashboard", element: <DashboardPage /> }],
    { initialEntries: ["/dashboard?account=910001"] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe("Dashboard Demo day selection", () => {
  beforeEach(() => {
    window.localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it.each([false, true])("filters a calendar day without journal requests (compact=%s)", async (compact) => {
    const createJournalEntry = vi.spyOn(accountsApi, "createJournalEntry");
    const getJournalDays = vi.spyOn(accountsApi, "getJournalDays");
    const router = renderDemoDashboard(compact);
    const name = compact ? /July 24, 2026,.*trade/i : /Jul 24, 2026\..*Net P&L/i;

    fireEvent.click(await screen.findByRole("button", { name }, { timeout: 10_000 }));

    await waitFor(() => expect(screen.getByRole("button", { name }).getAttribute("aria-pressed")).toBe("true"));
    expect(router.state.location.pathname).toBe("/dashboard");
    expect(router.state.location.search).toContain("account=910001");
    expect(screen.queryByRole("button", { name: /journal/i })).toBeNull();
    expect(getJournalDays).not.toHaveBeenCalled();
    expect(createJournalEntry).not.toHaveBeenCalled();
  }, 20_000);
});
