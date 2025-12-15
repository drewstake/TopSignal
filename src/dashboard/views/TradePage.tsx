import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { searchAccounts, type TopstepAccount } from "../../api/account";
import { hasSessionToken } from "../../lib/session";
import { fmtMoney } from "../../lib/format";
import MarketDataTicker from "../../market/MarketDataTicker";
import type { QuoteUpdate } from "../../market/TopstepXMarketData";

export type BotStatus = "stopped" | "running" | "deploying" | "error";

export type TradingBot = {
  id: string;
  name: string;
  strategy: string;
  accountId: number;
  instrument: string;
  status: BotStatus;
  dailyTarget: number;
  dailyMaxLoss: number;
  positionSize: number;
  lastAction: string;
  heartbeatMs: number;
};

type PendingAction = { botId: string; action: "start" | "stop" | "redeploy" } | null;

function statusBadge(status: BotStatus) {
  const map: Record<BotStatus, string> = {
    running: "bg-emerald-900/60 text-emerald-200 border-emerald-700",
    stopped: "bg-zinc-900/60 text-zinc-200 border-zinc-700",
    deploying: "bg-amber-900/50 text-amber-100 border-amber-700",
    error: "bg-rose-950/60 text-rose-100 border-rose-800",
  };

  return (
    <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${map[status]}`}>
      {status === "running" && "Running"}
      {status === "stopped" && "Stopped"}
      {status === "deploying" && "Deploying"}
      {status === "error" && "Error"}
    </span>
  );
}

const DEFAULT_BOTS: TradingBot[] = [
  {
    id: "bot-scaler-01",
    name: "XFA London Scalp",
    strategy: "Momentum scalper",
    accountId: 0,
    instrument: "6E",
    status: "running",
    dailyTarget: 450,
    dailyMaxLoss: 300,
    positionSize: 2,
    lastAction: "Running since 08:30 ET",
    heartbeatMs: 3500,
  },
  {
    id: "bot-swing-02",
    name: "Overnight Mean Revert",
    strategy: "Session fade",
    accountId: 0,
    instrument: "ES",
    status: "stopped",
    dailyTarget: 800,
    dailyMaxLoss: 500,
    positionSize: 1,
    lastAction: "Stopped manually",
    heartbeatMs: 0,
  },
  {
    id: "bot-break-03",
    name: "Breakout NY Open",
    strategy: "Range break",
    accountId: 0,
    instrument: "NQ",
    status: "deploying",
    dailyTarget: 600,
    dailyMaxLoss: 400,
    positionSize: 1,
    lastAction: "Pushing latest config...",
    heartbeatMs: 12000,
  },
];

export default function TradePage() {
  const instrumentOptions = useMemo(
    () => [
      { value: "NQ", label: "Nasdaq (NQ)", mdSymbol: "MNQ" },
      { value: "ES", label: "S&P (ES)", mdSymbol: "MES" },
      { value: "GC", label: "Gold (GC)", mdSymbol: "MGC" },
    ],
    [],
  );

  const [accounts, setAccounts] = useState<TopstepAccount[]>([]);
  const [bots, setBots] = useState<TradingBot[]>(DEFAULT_BOTS);
  const [selectedAccountId, setSelectedAccountId] = useState<number | "">("");
  const [form, setForm] = useState({
    name: "",
    strategy: "",
    instrument: "ES",
    dailyTarget: 500,
    dailyMaxLoss: 300,
    positionSize: 1,
  });
  const [error, setError] = useState<string | null>(null);
  const [loadingAccounts, setLoadingAccounts] = useState(false);
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [testerAccountId, setTesterAccountId] = useState<number | "">("");
  const [testerInstrument, setTesterInstrument] = useState<string>("NQ");
  const [testerRunning, setTesterRunning] = useState(false);
  const [testerLogs, setTesterLogs] = useState<string[]>([]);
  const [testerError, setTesterError] = useState<string | null>(null);
  const [latestQuote, setLatestQuote] = useState<QuoteUpdate | null>(null);

  const connected = hasSessionToken();

  useEffect(() => {
    if (!connected) {
      setAccounts([]);
      return;
    }

    async function loadAccounts() {
      setLoadingAccounts(true);
      try {
        const res = await searchAccounts({
          onlyActiveAccounts: true,
          includeInvisibleAccounts: false,
          cacheTtlMs: 30_000,
        });

        if (!res.success || res.errorCode !== 0) {
          throw new Error(res.errorMessage || `Failed to load accounts (errorCode ${res.errorCode}).`);
        }

        setAccounts(res.accounts || []);
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Unable to load accounts.";
        setError(msg);
      } finally {
        setLoadingAccounts(false);
      }
    }

    void loadAccounts();
  }, [connected]);

  useEffect(() => {
    if (accounts.length && bots.some((b) => b.accountId === 0)) {
      const fallbackId = accounts[0].id;
      setBots((prev) => prev.map((b) => (b.accountId === 0 ? { ...b, accountId: fallbackId } : b)));
    }
  }, [accounts, bots]);

  const totals = useMemo(() => {
    const running = bots.filter((b) => b.status === "running").length;
    const stopped = bots.filter((b) => b.status === "stopped").length;
    const deploying = bots.filter((b) => b.status === "deploying").length;
    const atRisk = bots.reduce((sum, b) => sum + b.dailyMaxLoss, 0);
    const target = bots.reduce((sum, b) => sum + b.dailyTarget, 0);

    return { running, stopped, deploying, atRisk, target };
  }, [bots]);

  const testerSymbol = useMemo(
    () => instrumentOptions.find((o) => o.value === testerInstrument)?.mdSymbol ?? testerInstrument,
    [instrumentOptions, testerInstrument],
  );

  const selectedTesterAccount = useMemo(
    () => accounts.find((a) => a.id === testerAccountId) || null,
    [accounts, testerAccountId],
  );

  useEffect(() => {
    if (accounts.length && testerAccountId === "") {
      setTesterAccountId(accounts[0].id);
    }
  }, [accounts, testerAccountId]);

  useEffect(() => {
    if (!connected && testerRunning) {
      setTesterRunning(false);
      setTesterError("Disconnected — stop the test bot until a session is active.");
    }
  }, [connected, testerRunning]);

  function updateBot(botId: string, updater: (b: TradingBot) => TradingBot) {
    setBots((prev) => prev.map((b) => (b.id === botId ? updater(b) : b)));
  }

  function handleAction(botId: string, action: Exclude<PendingAction, null>["action"]) {
    setPendingAction({ botId, action });

    setTimeout(() => {
      setPendingAction(null);
      if (action === "start") {
        updateBot(botId, (b) => ({
          ...b,
          status: "running",
          lastAction: `Started at ${new Date().toLocaleTimeString()}`,
          heartbeatMs: 2500,
        }));
      } else if (action === "stop") {
        updateBot(botId, (b) => ({
          ...b,
          status: "stopped",
          heartbeatMs: 0,
          lastAction: `Stopped at ${new Date().toLocaleTimeString()}`,
        }));
      } else {
        updateBot(botId, (b) => ({
          ...b,
          status: "deploying",
          lastAction: "Redeploying configuration...",
        }));
        setTimeout(() => {
          updateBot(botId, (b) => ({
            ...b,
            status: "running",
            lastAction: "Deployment complete",
            heartbeatMs: 2000,
          }));
        }, 1200);
      }
    }, 600);
  }

  const formatPrice = (value: number | null | undefined) => (value === null || value === undefined ? "--" : value.toFixed(2));

  function appendTesterLog(message: string) {
    setTesterLogs((prev) => {
      const next = [`[${new Date().toLocaleTimeString()}] ${message}`, ...prev];
      return next.slice(0, 20);
    });
  }

  function toggleTesterBot() {
    if (testerRunning) {
      appendTesterLog("Stopped random order flow.");
      setTesterRunning(false);
      return;
    }

    if (!connected) {
      setTesterError("Connect in Settings first.");
      return;
    }

    if (!testerAccountId) {
      setTesterError("Pick an account for the test bot.");
      return;
    }

    setTesterError(null);
    appendTesterLog(
      `Starting random ${testerInstrument} orders on ${
        selectedTesterAccount ? `${selectedTesterAccount.name} (${selectedTesterAccount.id})` : testerAccountId
      }`,
    );
    setTesterRunning(true);
  }

  useEffect(() => {
    if (!testerRunning) return undefined;

    const interval = setInterval(() => {
      const side = Math.random() > 0.5 ? "BUY" : "SELL";
      const size = Math.ceil(Math.random() * 2);
      const bestBid = latestQuote?.bestBid ?? null;
      const bestAsk = latestQuote?.bestAsk ?? null;
      const spread = latestQuote?.spread ?? (bestAsk && bestBid ? bestAsk - bestBid : null);

      const referencePrice =
        side === "BUY"
          ? bestAsk ?? bestBid ?? latestQuote?.last ?? null
          : bestBid ?? latestQuote?.last ?? bestAsk ?? null;

      const price = referencePrice ? Number(referencePrice.toFixed(2)) : Number((100 + Math.random() * 5).toFixed(2));
      const accountLabel = selectedTesterAccount
        ? `${selectedTesterAccount.name} (${selectedTesterAccount.id})`
        : `Account ${testerAccountId}`;

      appendTesterLog(
        `${side} ${size} ${testerInstrument} @ ${price.toFixed(2)} | bid ${formatPrice(bestBid)} / ask ${formatPrice(
          bestAsk,
        )} (spread ${formatPrice(spread)}) on ${accountLabel}`,
      );
    }, 2400);

    return () => clearInterval(interval);
  }, [latestQuote, selectedTesterAccount, testerAccountId, testerInstrument, testerRunning]);

  function handleCreateBot() {
    if (!connected) {
      setError("Connect in Settings first.");
      return;
    }

    if (!selectedAccountId) {
      setError("Pick an account to deploy against.");
      return;
    }

    const cleanName = form.name.trim();
    const cleanStrategy = form.strategy.trim();

    if (!cleanName || !cleanStrategy) {
      setError("Name and strategy are required.");
      return;
    }

    const newBot: TradingBot = {
      id: `bot-${Date.now()}`,
      name: cleanName,
      strategy: cleanStrategy,
      accountId: Number(selectedAccountId),
      instrument: form.instrument,
      status: "deploying",
      dailyTarget: Number(form.dailyTarget) || 0,
      dailyMaxLoss: Number(form.dailyMaxLoss) || 0,
      positionSize: Number(form.positionSize) || 1,
      lastAction: "Queued for deployment",
      heartbeatMs: 0,
    };

    setBots((prev) => [newBot, ...prev]);
    setForm({ name: "", strategy: "", instrument: "ES", dailyTarget: 500, dailyMaxLoss: 300, positionSize: 1 });
    setSelectedAccountId("");
    setError(null);

    setPendingAction({ botId: newBot.id, action: "redeploy" });
    setTimeout(() => {
      setPendingAction(null);
      updateBot(newBot.id, (b) => ({
        ...b,
        status: "running",
        lastAction: "Deployment complete",
        heartbeatMs: 2000,
      }));
    }, 1400);
  }

  return (
    <div className="grid grid-cols-1 gap-4">
      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-lg font-semibold text-zinc-100">Trade bots</div>
            <div className="mt-1 text-sm text-zinc-400">Deploy, monitor, and control automation for each account.</div>
          </div>
          <div className="flex items-center gap-2 text-xs text-zinc-400">
            <span className="h-2 w-2 rounded-full bg-emerald-500" /> Running bots: {totals.running}
            <span className="h-2 w-2 rounded-full bg-amber-400" /> Deploying: {totals.deploying}
            <span className="h-2 w-2 rounded-full bg-zinc-300" /> Stopped: {totals.stopped}
          </div>
        </div>

        {!connected ? (
          <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-sm text-zinc-200">
            You are not connected. Go to {" "}
            <Link to="/settings" className="underline">
              Settings
            </Link>{" "}
            and connect with your Project X credentials.
          </div>
        ) : null}

        {error ? (
          <div className="mt-4 rounded-xl border border-rose-900 bg-rose-950/60 px-3 py-2 text-sm text-rose-100">{error}</div>
        ) : null}

        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-4">
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Running</div>
            <div className="mt-1 text-2xl font-semibold text-emerald-100">{totals.running}</div>
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Deploying</div>
            <div className="mt-1 text-2xl font-semibold text-amber-100">{totals.deploying}</div>
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Daily target</div>
            <div className="mt-1 text-2xl font-semibold text-zinc-100">{fmtMoney(totals.target)}</div>
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Max risk on day</div>
            <div className="mt-1 text-2xl font-semibold text-rose-100">{fmtMoney(totals.atRisk)}</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-zinc-100">Strategy tester</div>
                <div className="text-xs text-zinc-400">Pick an account and NQ / ES / GC to fire random buys and sells.</div>
              </div>
              <button
                onClick={toggleTesterBot}
                className={`rounded-xl border px-3 py-2 text-xs font-semibold ${
                  testerRunning
                    ? "border-rose-600 bg-rose-950/60 text-rose-100 hover:border-rose-400"
                    : "border-emerald-700 bg-emerald-950/50 text-emerald-100 hover:border-emerald-400"
                }`}
              >
                {testerRunning ? "Stop random bot" : "Start random bot"}
              </button>
            </div>

            {testerError ? (
              <div className="mt-3 rounded-xl border border-amber-800 bg-amber-950/50 px-3 py-2 text-xs text-amber-100">{testerError}</div>
            ) : null}

            <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
              <label className="grid gap-1 text-sm">
                <span className="text-xs text-zinc-400">Test account</span>
                <select
                  className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                  value={testerAccountId}
                  onChange={(e) => setTesterAccountId(e.target.value ? Number(e.target.value) : "")}
                  disabled={!connected || loadingAccounts}
                >
                  <option value="">{loadingAccounts ? "Loading accounts..." : "Select account"}</option>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} ({a.id})
                    </option>
                  ))}
                </select>
              </label>

              <label className="grid gap-1 text-sm">
                <span className="text-xs text-zinc-400">Instrument</span>
                <select
                  className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                  value={testerInstrument}
                  onChange={(e) => setTesterInstrument(e.target.value)}
                >
                  {instrumentOptions.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-2 rounded-xl border border-zinc-800 bg-zinc-950/40 p-3 text-xs text-zinc-200 sm:grid-cols-4">
              <div>
                <div className="text-[11px] uppercase tracking-wide text-zinc-400">Last</div>
                <div className="text-base font-semibold text-zinc-100">{formatPrice(latestQuote?.last)}</div>
              </div>
              <div>
                <div className="text-[11px] uppercase tracking-wide text-zinc-400">Best Bid</div>
                <div className="text-base font-semibold text-emerald-100">{formatPrice(latestQuote?.bestBid)}</div>
              </div>
              <div>
                <div className="text-[11px] uppercase tracking-wide text-zinc-400">Best Ask</div>
                <div className="text-base font-semibold text-amber-100">{formatPrice(latestQuote?.bestAsk)}</div>
              </div>
              <div>
                <div className="text-[11px] uppercase tracking-wide text-zinc-400">Spread</div>
                <div className="text-base font-semibold text-zinc-100">{formatPrice(latestQuote?.spread)}</div>
              </div>
            </div>

            <div className="mt-3 rounded-xl border border-zinc-800 bg-zinc-950/30 p-3">
              <div className="text-xs font-semibold text-zinc-200">Random bot activity</div>
              <div className="mt-2 space-y-2 text-[11px] text-zinc-300">
                {testerLogs.length === 0 ? <div>No simulated orders yet.</div> : null}
                {testerLogs.map((line, idx) => (
                  <div key={idx} className="rounded-lg bg-zinc-900/70 px-3 py-2">
                    {line}
                  </div>
                ))}
              </div>
            </div>
          </div>

          <MarketDataTicker symbol={testerSymbol} label={`${testerInstrument} order flow`} onQuote={setLatestQuote} />
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-4">
          <div className="text-sm font-semibold text-zinc-100">Deploy new bot</div>
          <div className="text-xs text-zinc-400">Fill in the strategy profile and link an account.</div>

          <div className="mt-3 grid grid-cols-1 gap-3 text-sm">
            <label className="grid gap-1">
              <span className="text-xs text-zinc-400">Name</span>
              <input
                className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                value={form.name}
                onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
                placeholder="London breakout"
              />
            </label>

            <label className="grid gap-1">
              <span className="text-xs text-zinc-400">Strategy notes</span>
              <textarea
                className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                value={form.strategy}
                onChange={(e) => setForm((prev) => ({ ...prev, strategy: e.target.value }))}
                placeholder="Describe entry, exit, risk rules"
              />
            </label>

            <label className="grid gap-1">
              <span className="text-xs text-zinc-400">Account</span>
              <select
                className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                value={selectedAccountId}
                onChange={(e) => setSelectedAccountId(e.target.value ? Number(e.target.value) : "")}
                disabled={!connected || loadingAccounts}
              >
                <option value="">{loadingAccounts ? "Loading accounts..." : "Select account"}</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} ({a.id})
                  </option>
                ))}
              </select>
            </label>

            <div className="grid grid-cols-2 gap-2">
              <label className="grid gap-1">
                <span className="text-xs text-zinc-400">Instrument</span>
                <select
                  className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                  value={form.instrument}
                  onChange={(e) => setForm((prev) => ({ ...prev, instrument: e.target.value }))}
                >
                  {instrumentOptions.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1">
                <span className="text-xs text-zinc-400">Position size</span>
                <input
                  type="number"
                  className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                  value={form.positionSize}
                  onChange={(e) => setForm((prev) => ({ ...prev, positionSize: Number(e.target.value) }))}
                />
              </label>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <label className="grid gap-1">
                <span className="text-xs text-zinc-400">Daily target</span>
                <input
                  type="number"
                  className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                  value={form.dailyTarget}
                  onChange={(e) => setForm((prev) => ({ ...prev, dailyTarget: Number(e.target.value) }))}
                />
              </label>
              <label className="grid gap-1">
                <span className="text-xs text-zinc-400">Daily max loss</span>
                <input
                  type="number"
                  className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
                  value={form.dailyMaxLoss}
                  onChange={(e) => setForm((prev) => ({ ...prev, dailyMaxLoss: Number(e.target.value) }))}
                />
              </label>
            </div>

            <button
              onClick={handleCreateBot}
              disabled={pendingAction !== null}
              className="mt-2 rounded-xl border border-emerald-700 bg-emerald-950/50 px-3 py-2 text-sm font-semibold text-emerald-100 hover:border-emerald-400 disabled:opacity-50"
            >
              Deploy bot
            </button>

            <div className="rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-xs text-zinc-400">
              Deployments use the Project X Gateway API. Configure your API key in Settings. See {" "}
              <a
                href="https://gateway.docs.projectx.com/docs/intro"
                target="_blank"
                rel="noreferrer"
                className="text-emerald-200 underline"
              >
                Gateway docs
              </a>{" "}
              for supported endpoints like orders, positions, and risk controls.
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40">
        <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-zinc-100">Deployed bots</div>
            <div className="text-xs text-zinc-400">Start, stop, or redeploy configurations.</div>
          </div>
          {pendingAction ? (
            <div className="rounded-full border border-amber-700 bg-amber-900/50 px-3 py-1 text-xs text-amber-100">
              {pendingAction.action === "start" && "Starting bot..."}
              {pendingAction.action === "stop" && "Stopping bot..."}
              {pendingAction.action === "redeploy" && "Redeploying bot..."}
            </div>
          ) : null}
        </div>

        <div className="divide-y divide-zinc-800">
          {bots.map((bot, index) => {
            const account = accounts.find((a) => a.id === bot.accountId);

            return (
              <div
                key={bot.id}
                className={`grid grid-cols-1 gap-3 px-4 py-4 text-sm md:grid-cols-12 ${
                  index % 2 === 0 ? "bg-zinc-950/30" : "bg-transparent"
                }`}
              >
                <div className="md:col-span-4">
                  <div className="flex items-center gap-2">
                    <div className="font-semibold text-zinc-100">{bot.name}</div>
                    {statusBadge(bot.status)}
                  </div>
                  <div className="mt-1 text-xs text-zinc-400">{bot.strategy}</div>
                  <div className="mt-1 text-xs text-zinc-400">Last: {bot.lastAction}</div>
                </div>

                <div className="md:col-span-3">
                  <div className="text-xs text-zinc-400">Account</div>
                  <div className="text-sm text-zinc-100">{account ? `${account.name} (${account.id})` : "Unknown account"}</div>
                  <div className="mt-1 text-xs text-zinc-400">Instrument: {bot.instrument}</div>
                </div>

                <div className="md:col-span-3">
                  <div className="text-xs text-zinc-400">Targets & risk</div>
                  <div className="text-sm text-zinc-100">Target {fmtMoney(bot.dailyTarget)}</div>
                  <div className="text-xs text-rose-200">Max loss {fmtMoney(bot.dailyMaxLoss)}</div>
                  <div className="text-xs text-zinc-400">Size: {bot.positionSize} contracts</div>
                </div>

                <div className="flex items-center gap-2 md:col-span-2 md:justify-end">
                  <button
                    disabled={pendingAction !== null}
                    onClick={() => handleAction(bot.id, bot.status === "running" ? "stop" : "start")}
                    className="rounded-xl border border-zinc-700 bg-zinc-950/40 px-3 py-2 text-xs text-zinc-100 hover:border-zinc-500 disabled:opacity-50"
                  >
                    {bot.status === "running" ? "Stop" : "Start"}
                  </button>
                  <button
                    disabled={pendingAction !== null}
                    onClick={() => handleAction(bot.id, "redeploy")}
                    className="rounded-xl border border-zinc-700 bg-zinc-900/60 px-3 py-2 text-xs text-zinc-100 hover:border-amber-500 disabled:opacity-50"
                  >
                    Redeploy
                  </button>
                </div>

                <div className="md:col-span-12">
                  <div className="h-px bg-zinc-800" />
                  <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-zinc-400">
                    <span className="rounded-full bg-zinc-800 px-2 py-1">Heartbeat {bot.heartbeatMs ? `${bot.heartbeatMs} ms` : "n/a"}</span>
                    <span className="rounded-full bg-zinc-800 px-2 py-1">Instrument: {bot.instrument}</span>
                    <span className="rounded-full bg-zinc-800 px-2 py-1">Position size: {bot.positionSize}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
