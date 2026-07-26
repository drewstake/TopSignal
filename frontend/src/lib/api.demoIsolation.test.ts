// @vitest-environment jsdom

import { waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { getAccessTokenMock, getDemoApiResponseMock } = vi.hoisted(() => ({
  getAccessTokenMock: vi.fn<() => Promise<string | null>>(async () => null),
  getDemoApiResponseMock: vi.fn(() => null as { data: unknown } | null),
}));

vi.mock("./supabase", () => ({
  getAccessToken: getAccessTokenMock,
}));

vi.mock("./demoData", () => ({
  getDemoApiResponse: getDemoApiResponseMock,
}));

import {
  accountsApi,
  botsApi,
  requestBlob,
  streamProjectXMarketDepth,
  streamProjectXMarketPrice,
} from "./api";
import { setDemoModeEnabled } from "./demoMode";

describe("Demo Mode transport isolation", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setDemoModeEnabled(true);
    getAccessTokenMock.mockReset();
    getAccessTokenMock.mockResolvedValue(null);
    getDemoApiResponseMock.mockReset();
    getDemoApiResponseMock.mockReturnValue(null);
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    setDemoModeEnabled(false);
    window.localStorage.clear();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("fails closed for a JSON route without a fixture before token or fetch access", async () => {
    await expect(accountsApi.getAuthMe()).rejects.toThrow(
      "No demonstration data is available for /api/auth/me. No live request was sent.",
    );

    expect(getAccessTokenMock).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("blocks blob downloads before token or fetch access", async () => {
    await expect(requestBlob("/api/accounts/910001/journal/22/images/3/content")).rejects.toThrow(
      "File download is unavailable in Demo Mode",
    );

    expect(getAccessTokenMock).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("blocks multipart imports before token or fetch access", async () => {
    const file = new File(["timestamp,symbol,pnl"], "trades.csv", { type: "text/csv" });

    await expect(accountsApi.previewTradeImport(910001, file)).rejects.toThrow("Demo mode is read-only");

    expect(getAccessTokenMock).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("reports unavailable price and depth streams without opening provider connections", async () => {
    const onPriceError = vi.fn();
    const onDepthState = vi.fn();

    const closePrice = streamProjectXMarketPrice(
      { contractId: "CON.F.US.MNQ.U26", symbol: "MNQ" },
      { onPrice: vi.fn(), onError: onPriceError },
    );
    const closeDepth = streamProjectXMarketDepth(
      { contractId: "CON.F.US.MNQ.U26" },
      { onState: onDepthState, onSnapshot: vi.fn(), onUpdate: vi.fn() },
    );
    await Promise.resolve();

    expect(onPriceError).toHaveBeenCalledWith(expect.objectContaining({ status: 409 }));
    expect(onDepthState).toHaveBeenCalledWith({
      contract_id: "CON.F.US.MNQ.U26",
      state: "unavailable",
      message: "Live market depth is unavailable in Demo Mode.",
    });
    expect(getAccessTokenMock).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();

    closePrice();
    closeDepth();
  });

  it("blocks both backtest transports before authentication", async () => {
    const input = {
      starting_balance: 50_000,
      commission_per_contract: 1.2,
      slippage_ticks: 1,
    };

    await expect(botsApi.runBacktest(7, input)).rejects.toThrow("Demo mode is read-only");
    await expect(botsApi.runBacktest(7, input, { onProgress: vi.fn() })).rejects.toThrow(
      "Demo mode is read-only",
    );
    expect(getAccessTokenMock).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("cancels an in-flight live JSON request when Demo Mode is enabled", async () => {
    setDemoModeEnabled(false);
    getAccessTokenMock.mockResolvedValue("live-token");
    let fetchSignal: AbortSignal | undefined;
    vi.mocked(fetch).mockImplementation((_input, init) => {
      fetchSignal = init?.signal ?? undefined;
      return new Promise<Response>((_resolve, reject) => {
        fetchSignal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      });
    });

    const pending = accountsApi.getAuthMe();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    setDemoModeEnabled(true);

    expect(fetchSignal?.aborted).toBe(true);
    await expect(pending).rejects.toThrow("Live data access was stopped when Demo Mode was enabled");
  });

  it("cancels in-flight multipart and blob transports when Demo Mode is enabled", async () => {
    const starts = [
      () => accountsApi.previewTradeImport(910001, new File(["trade"], "trades.csv", { type: "text/csv" })),
      () => requestBlob("/api/accounts/910001/journal/22/images/3/content"),
    ];

    for (const start of starts) {
      setDemoModeEnabled(false);
      getAccessTokenMock.mockResolvedValue("live-token");
      let fetchSignal: AbortSignal | undefined;
      vi.mocked(fetch).mockReset();
      vi.mocked(fetch).mockImplementation((_input, init) => {
        fetchSignal = init?.signal ?? undefined;
        return new Promise<Response>((_resolve, reject) => {
          fetchSignal?.addEventListener(
            "abort",
            () => reject(new DOMException("Aborted", "AbortError")),
            { once: true },
          );
        });
      });

      const pending = start();
      await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
      setDemoModeEnabled(true);

      expect(fetchSignal?.aborted).toBe(true);
      await expect(pending).rejects.toMatchObject({ status: 409 });
    }
  });

  it("rejects an abort-ignoring 204 mutation response after Demo activation", async () => {
    setDemoModeEnabled(false);
    getAccessTokenMock.mockResolvedValue("live-token");
    let resolveFetch!: (response: Response) => void;
    let fetchSignal: AbortSignal | undefined;
    vi.mocked(fetch).mockImplementation((_input, init) => {
      fetchSignal = init?.signal ?? undefined;
      return new Promise<Response>((resolve) => {
        resolveFetch = resolve;
      });
    });

    const pending = accountsApi.deleteProjectXCredentials();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    setDemoModeEnabled(true);
    expect(fetchSignal?.aborted).toBe(true);
    resolveFetch(new Response(null, { status: 204 }));

    await expect(pending).rejects.toThrow("Live data access was stopped when Demo Mode was enabled");
  });

  it("does not call fetch when Demo Mode wins a pending token race", async () => {
    setDemoModeEnabled(false);
    let resolveToken!: (token: string | null) => void;
    getAccessTokenMock.mockReturnValue(new Promise((resolve) => {
      resolveToken = resolve;
    }));

    const pending = accountsApi.getAuthMe();
    setDemoModeEnabled(true);
    resolveToken("late-live-token");

    await expect(pending).rejects.toThrow("Live data access was stopped when Demo Mode was enabled");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("wins pending-token races for multipart, blob, and streamed backtest transports", async () => {
    const input = {
      starting_balance: 50_000,
      commission_per_contract: 1.2,
      slippage_ticks: 1,
    };
    const starts: Array<() => Promise<unknown>> = [
      () => accountsApi.previewTradeImport(910001, new File(["trade"], "trades.csv", { type: "text/csv" })),
      () => requestBlob("/api/accounts/910001/journal/22/images/3/content"),
      () => botsApi.runBacktest(7, input, { onProgress: vi.fn() }),
    ];

    for (const start of starts) {
      setDemoModeEnabled(false);
      let resolveToken!: (token: string | null) => void;
      getAccessTokenMock.mockReset();
      getAccessTokenMock.mockReturnValue(
        new Promise((resolve) => {
          resolveToken = resolve;
        }),
      );

      const pending = start();
      await waitFor(() => expect(getAccessTokenMock).toHaveBeenCalledTimes(1));
      setDemoModeEnabled(true);
      resolveToken("late-live-token");

      await expect(pending).rejects.toMatchObject({ status: 409 });
      expect(fetch).not.toHaveBeenCalled();
    }
  });

  it("aborts both active backtest transports without delivering progress", async () => {
    const input = {
      starting_balance: 50_000,
      commission_per_contract: 1.2,
      slippage_ticks: 1,
    };

    for (const streamed of [false, true]) {
      setDemoModeEnabled(false);
      getAccessTokenMock.mockReset();
      getAccessTokenMock.mockResolvedValue("live-token");
      vi.mocked(fetch).mockReset();
      const onProgress = vi.fn();
      let fetchSignal: AbortSignal | undefined;
      vi.mocked(fetch).mockImplementation((_input, init) => {
        fetchSignal = init?.signal ?? undefined;
        return new Promise<Response>((_resolve, reject) => {
          fetchSignal?.addEventListener(
            "abort",
            () => reject(new DOMException("Aborted", "AbortError")),
            { once: true },
          );
        });
      });

      const pending = botsApi.runBacktest(7, input, streamed ? { onProgress } : undefined);
      await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
      setDemoModeEnabled(true);

      expect(fetchSignal?.aborted).toBe(true);
      await expect(pending).rejects.toMatchObject({ status: 409 });
      expect(onProgress).not.toHaveBeenCalled();
    }
  });

  it("aborts active provider streams and does not enter a reconnect loop", async () => {
    setDemoModeEnabled(false);
    getAccessTokenMock.mockResolvedValue("live-token");
    const signals: AbortSignal[] = [];
    vi.mocked(fetch).mockImplementation((_input, init) => {
      const signal = init?.signal;
      if (signal) {
        signals.push(signal);
      }
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      });
    });
    const priceError = vi.fn();
    const depthState = vi.fn();

    const closePrice = streamProjectXMarketPrice(
      { contractId: "CON.F.US.MNQ.U26", symbol: "MNQ" },
      { onPrice: vi.fn(), onError: priceError },
    );
    const closeDepth = streamProjectXMarketDepth(
      { contractId: "CON.F.US.MNQ.U26" },
      { onState: depthState, onSnapshot: vi.fn(), onUpdate: vi.fn() },
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));

    setDemoModeEnabled(true);

    expect(signals).toHaveLength(2);
    expect(signals.every((signal) => signal.aborted)).toBe(true);
    expect(priceError).toHaveBeenCalledWith(expect.objectContaining({ status: 409 }));
    expect(depthState).toHaveBeenCalledWith(expect.objectContaining({
      state: "unavailable",
      message: "Live market depth is unavailable in Demo Mode.",
    }));
    await Promise.resolve();
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(2);

    closePrice();
    closeDepth();
  });

  it("suppresses buffered price and depth frames after Demo activation", async () => {
    setDemoModeEnabled(false);
    getAccessTokenMock.mockResolvedValue("live-token");
    const encoder = new TextEncoder();
    let priceController!: ReadableStreamDefaultController<Uint8Array>;
    let depthController!: ReadableStreamDefaultController<Uint8Array>;
    vi.mocked(fetch).mockImplementation((input) => {
      const isDepth = String(input).includes("market-depth");
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          if (isDepth) {
            depthController = controller;
          } else {
            priceController = controller;
          }
        },
      });
      return Promise.resolve(new Response(stream, { status: 200 }));
    });
    const onPrice = vi.fn();
    const onPriceError = vi.fn();
    const onDepthState = vi.fn();
    const onDepthSnapshot = vi.fn();
    const onDepthUpdate = vi.fn();

    const closePrice = streamProjectXMarketPrice(
      { contractId: "CON.F.US.MNQ.U26", symbol: "MNQ" },
      { onPrice, onError: onPriceError },
    );
    const closeDepth = streamProjectXMarketDepth(
      { contractId: "CON.F.US.MNQ.U26" },
      { onState: onDepthState, onSnapshot: onDepthSnapshot, onUpdate: onDepthUpdate },
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));

    setDemoModeEnabled(true);
    priceController.enqueue(
      encoder.encode(
        'event: price\ndata: {"contract_id":"CON.F.US.MNQ.U26","symbol":"MNQ","price":19875.25,"timestamp":"2026-07-24T15:30:00.000Z"}\n\n',
      ),
    );
    depthController.enqueue(
      encoder.encode(
        'event: snapshot\ndata: {"contract_id":"CON.F.US.MNQ.U26","sequence":42,"timestamp":"2026-07-24T15:30:00.000Z","bids":[{"price":19875,"size":7}],"asks":[{"price":19875.5,"size":5}],"reset":true}\n\n',
      ),
    );
    priceController.close();
    depthController.close();
    await Promise.resolve();
    await Promise.resolve();

    expect(onPrice).not.toHaveBeenCalled();
    expect(onDepthSnapshot).not.toHaveBeenCalled();
    expect(onDepthUpdate).not.toHaveBeenCalled();
    expect(onPriceError).toHaveBeenCalledWith(
      expect.objectContaining({ message: "Live market price streaming was stopped when Demo Mode was enabled." }),
    );
    expect(onDepthState).toHaveBeenCalledWith(
      expect.objectContaining({ state: "unavailable", message: "Live market depth is unavailable in Demo Mode." }),
    );

    closePrice();
    closeDepth();
  });

  it("suppresses buffered backtest progress and results after Demo activation", async () => {
    setDemoModeEnabled(false);
    getAccessTokenMock.mockResolvedValue("live-token");
    const encoder = new TextEncoder();
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    let fetchSignal: AbortSignal | undefined;
    vi.mocked(fetch).mockImplementation((_input, init) => {
      fetchSignal = init?.signal ?? undefined;
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          streamController = controller;
        },
      });
      return Promise.resolve(new Response(stream, { status: 200 }));
    });
    const onProgress = vi.fn();
    const pending = botsApi.runBacktest(
      7,
      { starting_balance: 50_000, commission_per_contract: 1.2, slippage_ticks: 1 },
      { onProgress },
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));

    setDemoModeEnabled(true);
    expect(fetchSignal?.aborted).toBe(true);
    streamController.enqueue(encoder.encode('event: progress\ndata: {"phase":"replay","percent":80}\n\n'));
    streamController.enqueue(encoder.encode('event: result\ndata: {"net_pnl":999999}\n\n'));
    streamController.close();

    await expect(pending).rejects.toMatchObject({ status: 409 });
    expect(onProgress).not.toHaveBeenCalled();
  });

  it("keeps live cached reads out of the Demo cache lane", async () => {
    setDemoModeEnabled(false);
    getAccessTokenMock.mockResolvedValue(null);
    const liveAccounts = [{ id: 7301, name: "LIVE-ACCOUNT" }];
    const demoAccounts = [{ id: 910001, name: "DEMO-ACCOUNT" }];
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify(liveAccounts), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const options = {
      showInactive: false,
      showMissing: true,
      refreshProvider: false,
      includeArchived: true,
    };

    await expect(accountsApi.getAccounts(options)).resolves.toEqual(liveAccounts);
    expect(fetch).toHaveBeenCalledTimes(1);
    setDemoModeEnabled(true);
    getDemoApiResponseMock.mockReturnValue({ data: demoAccounts });

    await expect(accountsApi.getAccounts(options)).resolves.toEqual(demoAccounts);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(getAccessTokenMock).toHaveBeenCalledTimes(1);
  });

  it("rechecks Demo Mode after an opaque-token scope digest before selecting a cache lane", async () => {
    setDemoModeEnabled(false);
    getAccessTokenMock.mockResolvedValue("opaque-live-token-for-demo-race");
    const digestBytes = new Uint8Array([11, 22, 33, 44]).buffer;
    let resolveSecondDigest!: (value: ArrayBuffer) => void;
    const digest = vi
      .fn()
      .mockResolvedValueOnce(digestBytes)
      .mockReturnValueOnce(new Promise<ArrayBuffer>((resolve) => {
        resolveSecondDigest = resolve;
      }));
    vi.stubGlobal("crypto", {
      randomUUID: () => "api-demo-isolation-tab",
      subtle: { digest },
    });

    const liveAccounts = [{ id: 7319, name: "LIVE-OPAQUE-CACHE" }];
    const demoAccounts = [{ id: 910001, name: "DEMO-ACCOUNT" }];
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify(liveAccounts), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const options = {
      showInactive: true,
      showMissing: true,
      refreshProvider: false,
      includeArchived: true,
    };

    await expect(accountsApi.getAccounts(options)).resolves.toEqual(liveAccounts);
    expect(fetch).toHaveBeenCalledTimes(1);

    const pending = accountsApi.getAccounts(options);
    await waitFor(() => expect(digest).toHaveBeenCalledTimes(2));
    setDemoModeEnabled(true);
    getDemoApiResponseMock.mockReturnValue({ data: demoAccounts });
    resolveSecondDigest(digestBytes);

    await expect(pending).resolves.toEqual(demoAccounts);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
