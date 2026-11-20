// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, requestJson } from "../../lib/api";
import HealthPage from "./HealthPage";

vi.mock("../../lib/api", async original => ({ ...await original<typeof import("../../lib/api")>(), requestJson: vi.fn() }));
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); localStorage.clear(); });

const sample = "2026-11-20T08:00:00Z";
const report = {
  as_of: sample, components: {
    postgres: { status: "Healthy", reason: "PostgreSQL answered.", checked_at: sample },
    discord: { status: "Unhealthy", reason: "The reader is retrying.", checked_at: sample },
  },
};
function row(name: string) {
  return within(screen.getByRole("heading", { name }).closest("li")!);
}
function count(status: string) {
  const overview = within(screen.getByRole("region", { name: "Status overview" }));
  return overview.getByText(status).nextElementSibling?.textContent;
}

it("keeps every component visible and puts confirmed failures ahead of unknown and healthy checks", async () => {
  vi.mocked(requestJson).mockImplementation(async path => path === "/health" ? { status: "ok" } : report);
  render(<HealthPage />);
  await screen.findByText("The reader is retrying.");
  const rows = screen.getAllByRole("listitem");
  expect(rows).toHaveLength(8);
  expect([count("Unhealthy"), count("Unknown"), count("Healthy")]).toEqual(["1", "5", "2"]);
  expect(within(rows[0]).getByText("Discord reader")).toBeTruthy();
  expect(within(rows[1]).getByText("Unknown")).toBeTruthy();
  expect(within(rows.at(-1)!).getByText("Healthy")).toBeTruthy();
  expect(row("Discord reader").getByText(/Nov 20/).getAttribute("datetime")).toBe(sample);

  vi.mocked(requestJson).mockImplementation(async path => {
    if (path === "/health") throw new ApiError("unavailable", 503, {}, null);
    throw new Error("disconnected");
  });
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(row("API").getByText("Unhealthy")).toBeTruthy());
  expect(row("PostgreSQL").getByText("Unknown")).toBeTruthy();
  expect(row("PostgreSQL").getByText(/Nov 20/).getAttribute("datetime")).toBe(sample);
});

it("expires an individual observation while a healthy API is still current", async () => {
  vi.useFakeTimers();
  vi.mocked(requestJson).mockImplementation(async path => path === "/health" ? { status: "ok" } : {
    ...report, components: { postgres: { ...report.components.postgres, expires_at: "2026-11-20T08:00:02Z" } },
  });
  await act(async () => { render(<HealthPage />); });
  expect(row("PostgreSQL").getByText("Healthy")).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
  expect(row("PostgreSQL").getByText("Unknown")).toBeTruthy();
  expect(row("API").getByText("Healthy")).toBeTruthy();
  expect(count("Healthy")).toBe("1");
});

it("keeps live transport closed in Demo Mode", () => {
  localStorage.setItem("topsignal.demoMode", "true");
  render(<HealthPage />);
  expect(within(screen.getByRole("list", { name: "Component health" })).getAllByText("Unknown")).toHaveLength(8);
  expect(count("Unknown")).toBe("8");
  expect(requestJson).not.toHaveBeenCalled();
});
