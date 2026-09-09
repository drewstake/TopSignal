// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../../lib/api";
import { BotSignalChart } from "./BotSignalChart";
import { ProjectXSignalChart } from "./ProjectXSignalChart";

vi.mock("./BotSignalChart", () => ({ BotSignalChart: vi.fn(() => <div>Connected market chart</div>) }));
const contract = { id: "CON.F.US.MNQ.U26", name: "MNQU6", description: null, tick_size: 0.25,
  tick_value: 0.5, active_contract: true, symbol_id: "F.US.MNQ" };
beforeEach(() => {
  vi.spyOn(api, "getAuthenticatedCacheScope").mockResolvedValue("user:chart-viewer");
  vi.spyOn(api.botsApi, "searchContracts").mockResolvedValue([contract]);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks(); });

it("resolves the active MNQ contract and mounts the real chart without a synthetic bot or account", async () => {
  vi.mocked(api.botsApi.searchContracts).mockResolvedValue([
    { ...contract, id: "CON.F.US.MNQ.M26", active_contract: false },
    { ...contract, id: "CON.F.US.NQ.U26", name: "NQU6", symbol_id: "F.US.NQ" }, contract,
  ]);
  render(<ProjectXSignalChart enabled />);
  await screen.findByText("Connected market chart");
  const props = vi.mocked(BotSignalChart).mock.calls.at(-1)![0];
  expect(props.bot).toBeNull();
  expect(props.market?.contract_id).toBe(contract.id);
  expect(props.market).not.toHaveProperty("account_id");
  expect(props.market).not.toHaveProperty("id");
  expect(props.authenticatedCacheScope).toBe("user:chart-viewer");
  expect(props.activity).toBeNull();
  expect(props.lastEvaluation).toBeNull();
});

it("does not open a connection when disabled", async () => {
  render(<ProjectXSignalChart enabled={false} />);
  await act(async () => {});
  expect(api.botsApi.searchContracts).not.toHaveBeenCalled();
  expect(api.getAuthenticatedCacheScope).not.toHaveBeenCalled();
  expect(BotSignalChart).not.toHaveBeenCalled();
});

it("shares the resolved market and clears it when market data is disabled", async () => {
  const onMarketResolved = vi.fn();
  const view = render(<ProjectXSignalChart enabled onMarketResolved={onMarketResolved} />);
  await screen.findByText("Connected market chart");
  expect(onMarketResolved).toHaveBeenLastCalledWith(expect.objectContaining({ contract_id: contract.id }));
  view.rerender(<ProjectXSignalChart enabled={false} onMarketResolved={onMarketResolved} />);
  expect(onMarketResolved).toHaveBeenLastCalledWith(null);
});

it("shows the provider error and lets the user retry", async () => {
  vi.mocked(api.botsApi.searchContracts).mockRejectedValueOnce(new Error("ProjectX credentials unavailable"));
  render(<ProjectXSignalChart enabled />);
  expect((await screen.findByRole("alert")).textContent).toContain("ProjectX credentials unavailable");
  expect(BotSignalChart).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Retry chart connection" }));
  await screen.findByText("Connected market chart");
});

it("clears the previous market before reconnecting after Demo mode", async () => {
  const onMarketResolved = vi.fn();
  const view = render(<ProjectXSignalChart enabled onMarketResolved={onMarketResolved} />);
  await screen.findByText("Connected market chart");
  view.rerender(<ProjectXSignalChart enabled={false} onMarketResolved={onMarketResolved} />);
  let resolve!: (rows: typeof contract[]) => void;
  vi.mocked(api.botsApi.searchContracts).mockReturnValue(new Promise(done => { resolve = done; }));
  view.rerender(<ProjectXSignalChart enabled onMarketResolved={onMarketResolved} />);
  expect(screen.queryByText("Connected market chart")).toBeNull();
  expect(onMarketResolved).toHaveBeenLastCalledWith(null);
  await act(async () => { resolve([contract]); });
  expect(screen.getByText("Connected market chart")).toBeTruthy();
});

it("reports missing active contracts instead of guessing a contract", async () => {
  vi.mocked(api.botsApi.searchContracts).mockResolvedValue([{ ...contract, active_contract: false }]);
  render(<ProjectXSignalChart enabled />);
  expect((await screen.findByRole("alert")).textContent).toContain("no active MNQ contract");
  expect(BotSignalChart).not.toHaveBeenCalled();
});

it("explains an empty provider response and reconnects after access is restored", async () => {
  vi.mocked(api.botsApi.searchContracts).mockResolvedValueOnce([]);
  render(<ProjectXSignalChart enabled />);
  expect((await screen.findByRole("alert")).textContent).toContain("no MNQ contracts for the configured connection");
  expect(screen.queryByRole("link", { name: "Accounts" })).toBeNull();
  expect(BotSignalChart).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Retry chart connection" }));
  await screen.findByText("Connected market chart");
});

it("aborts discovery on unmount and ignores a late response", async () => {
  let resolve!: (rows: typeof contract[]) => void;
  vi.mocked(api.botsApi.searchContracts).mockReturnValue(new Promise(done => { resolve = done; }));
  const view = render(<ProjectXSignalChart enabled />);
  const signal = vi.mocked(api.botsApi.searchContracts).mock.calls[0][1]?.signal;
  view.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => { resolve([contract]); });
  expect(BotSignalChart).not.toHaveBeenCalled();
});
