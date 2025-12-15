import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { searchAccounts, type TopstepAccount } from "../../api/account";
import { hasSessionToken } from "../../lib/session";
import MarketDataTicker from "../../market/MarketDataTicker";
import type { QuoteUpdate } from "../../market/TopstepXMarketData";

const instrumentOptions = [
  { value: "NQ", label: "Nasdaq (NQ)", mdSymbol: "ENQ" },
  { value: "ES", label: "S&P (ES)", mdSymbol: "EP" },
  { value: "GC", label: "Gold (GC)", mdSymbol: "GC" },
];

const strategyOptions = [{ value: "liquidity-sweeps", label: "Liquidity Sweeps" }];

const liquiditySweepsPlaybook = {
  headline: "Liquidity sweep playbook",
  intro:
    "Scan historical bars for a sweep-and-reject pattern where price trades through a prior swing level and quickly reclaims it.",
  timeframes: [
    { label: "15m", unit: 2, unitNumber: 15 },
    { label: "1h", unit: 3, unitNumber: 1 },
    { label: "4h", unit: 3, unitNumber: 4 },
    { label: "1d", unit: 4, unitNumber: 1 },
  ],
  steps: [
    {
      title: "Define the pool (L1)",
      description:
        "Find a swing high/low: the extreme inside a small window (e.g., 3-5 bars on 15m, 2-4 on 1h).",
    },
    {
      title: "Wait for the sweep (L2)",
      description:
        "A later bar must take the level (low below L1 for bullish, high above L1 for bearish) and then close back beyond it.",
    },
    {
      title: "Score the timing",
      description:
        "Use a range, not one cutoff. Favor retests a session or a few sessions later; deprioritize sweeps that are too fresh or too stale.",
    },
    {
      title: "Check rejection quality",
      description:
        "Look for a decisive reclaim: sizable wick through L1, close away from the swept extreme, and optional volume pop vs recent bars.",
    },
    {
      title: "Optional time-of-day boost",
      description:
        "If L1 and L2 occur near the same clock time (e.g., both at the session open), bump the score without making it a hard rule.",
    },
  ],
};

const retrieveBarsParameters = [
  { name: "contractId", type: "string", description: "The contract ID." },
  { name: "live", type: "boolean", description: "Use the live data subscription instead of simulated." },
  { name: "startTime", type: "datetime", description: "Start time for the historical window." },
  { name: "endTime", type: "datetime", description: "End time for the historical window." },
  {
    name: "unit",
    type: "integer",
    description: "Aggregation unit: 1=Second, 2=Minute, 3=Hour, 4=Day, 5=Week, 6=Month.",
  },
  { name: "unitNumber", type: "integer", description: "How many units to aggregate into each bar." },
  { name: "limit", type: "integer", description: "Maximum number of bars to return (max 20,000)." },
  { name: "includePartialBar", type: "boolean", description: "Include the partial bar for the current interval." },
];

const retrieveBarsCurl = `curl -X 'POST' \\
  'https://api.topstepx.com/api/History/retrieveBars' \\
  -H 'accept: text/plain' \\
  -H 'Content-Type: application/json' \\
  -d '{
    "contractId": "CON.F.US.RTY.Z24",
    "live": false,
    "startTime": "2024-12-01T00:00:00Z",
    "endTime": "2024-12-31T21:00:00Z",
    "unit": 3,
    "unitNumber": 1,
    "limit": 7,
    "includePartialBar": false
  }'`;

const retrieveBarsResponse = `{
  "bars": [
    {
      "t": "2024-12-20T14:00:00+00:00",
      "o": 2208.100000000,
      "h": 2217.000000000,
      "l": 2206.700000000,
      "c": 2210.100000000,
      "v": 87
    },
    {
      "t": "2024-12-20T13:00:00+00:00",
      "o": 2195.800000000,
      "h": 2215.000000000,
      "l": 2192.900000000,
      "c": 2209.800000000,
      "v": 536
    }
  ],
  "success": true,
  "errorCode": 0,
  "errorMessage": null
}`;

function formatPrice(value: number | null | undefined) {
  if (value === null || value === undefined) return "--";
  return value.toFixed(2);
}

export default function TradePage() {
  const connected = hasSessionToken();

  const [accounts, setAccounts] = useState<TopstepAccount[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState<number | "">("");
  const [selectedInstrument, setSelectedInstrument] = useState(instrumentOptions[0].value);
  const [selectedStrategy, setSelectedStrategy] = useState(strategyOptions[0].value);
  const [contractSize, setContractSize] = useState(1);
  const [latestQuote, setLatestQuote] = useState<QuoteUpdate | null>(null);
  const [loadingAccounts, setLoadingAccounts] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeInstrument = useMemo(
    () => instrumentOptions.find((item) => item.value === selectedInstrument) ?? instrumentOptions[0],
    [selectedInstrument],
  );

  const priceSummary = useMemo(() => {
    const last = latestQuote?.last ?? null;
    const bid = latestQuote?.bestBid ?? null;
    const ask = latestQuote?.bestAsk ?? null;
    const spread = latestQuote?.spread ?? (bid !== null && ask !== null ? ask - bid : null);
    const reference = last ?? bid ?? ask ?? null;

    return {
      last: formatPrice(last),
      bid: formatPrice(bid),
      ask: formatPrice(ask),
      spread: formatPrice(spread),
      reference,
    };
  }, [latestQuote]);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!connected) {
      setAccounts([]);
      setSelectedAccountId("");
      return;
    }

    setLoadingAccounts(true);
    setError(null);

    searchAccounts({
      onlyActiveAccounts: true,
      includeInvisibleAccounts: false,
      cacheTtlMs: 30_000,
    })
      .then((res) => {
        if (!res.success || res.errorCode !== 0) {
          throw new Error(res.errorMessage || `Failed to load accounts (errorCode ${res.errorCode}).`);
        }

        setAccounts(res.accounts || []);
      })
      .catch((err) => {
        const message = err instanceof Error ? err.message : "Unable to load accounts.";
        setError(message);
      })
      .finally(() => setLoadingAccounts(false));
  }, [connected]);

  useEffect(() => {
    if (accounts.length && selectedAccountId === "") {
      setSelectedAccountId(accounts[0].id);
    }
  }, [accounts, selectedAccountId]);
  /* eslint-enable react-hooks/set-state-in-effect */

  return (
    <div className="grid grid-cols-1 gap-4">
      <div className="rounded-2xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900/40 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">Trade</div>
            <div className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
              Configure an order ticket with a clean, focused layout.
            </div>
          </div>
          {!connected ? (
            <div className="rounded-full border border-amber-700 bg-amber-900/60 px-3 py-1 text-xs text-amber-100">
              Not connected
            </div>
          ) : null}
        </div>

        {!connected ? (
          <div className="mt-3 rounded-xl border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/30 px-3 py-2 text-sm text-zinc-800 dark:text-zinc-200">
            You are not connected. Go to{" "}
            <Link to="/settings" className="underline">
              Settings
            </Link>{" "}
            and connect with your Project X credentials.
          </div>
        ) : null}

        {error ? (
          <div className="mt-3 rounded-xl border border-rose-900 bg-rose-950/60 px-3 py-2 text-sm text-rose-100">{error}</div>
        ) : null}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <div className="rounded-2xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900/40 p-5">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Order ticket</div>
                <div className="text-xs text-zinc-600 dark:text-zinc-400">Pick the essentials to route an order.</div>
              </div>
              <div className="rounded-full border border-emerald-800 bg-emerald-900/40 px-3 py-1 text-[11px] font-semibold text-emerald-100">
                Live price: {priceSummary.reference !== null ? formatPrice(priceSummary.reference) : "--"}
              </div>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
              <label className="grid gap-1 text-sm">
                <span className="text-xs text-zinc-600 dark:text-zinc-400">Account</span>
                <select
                  className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100"
                  value={selectedAccountId}
                  onChange={(e) => setSelectedAccountId(e.target.value ? Number(e.target.value) : "")}
                  disabled={!connected || loadingAccounts}
                >
                  <option value="">{loadingAccounts ? "Loading accounts..." : "Select account"}</option>
                  {accounts.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.name} ({account.id})
                    </option>
                  ))}
                </select>
              </label>

              <label className="grid gap-1 text-sm">
                <span className="text-xs text-zinc-600 dark:text-zinc-400">Strategy</span>
                <select
                  className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100"
                  value={selectedStrategy}
                  onChange={(e) => setSelectedStrategy(e.target.value)}
                >
                  {strategyOptions.map((strategy) => (
                    <option key={strategy.value} value={strategy.value}>
                      {strategy.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="grid gap-1 text-sm">
                <span className="text-xs text-zinc-600 dark:text-zinc-400">Instrument</span>
                <select
                  className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100"
                  value={selectedInstrument}
                  onChange={(e) => setSelectedInstrument(e.target.value)}
                >
                  {instrumentOptions.map((instrument) => (
                    <option key={instrument.value} value={instrument.value}>
                      {instrument.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="grid gap-1 text-sm">
                <span className="text-xs text-zinc-600 dark:text-zinc-400">Contract size</span>
                <div className="flex items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2">
                  <input
                    type="range"
                    min={1}
                    max={10}
                    value={contractSize}
                    onChange={(e) => setContractSize(Number(e.target.value))}
                    className="h-2 w-full accent-emerald-400"
                  />
                  <span className="w-12 text-center text-sm text-zinc-900 dark:text-zinc-100">{contractSize}x</span>
                </div>
              </label>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 rounded-xl border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/30 p-3 sm:grid-cols-3">
              <SummaryTile label="Selected account" value={selectedAccountId ? `#${selectedAccountId}` : "None"} />
              <SummaryTile label="Strategy" value={strategyOptions.find((s) => s.value === selectedStrategy)?.label ?? "--"} />
              <SummaryTile label="Size" value={`${contractSize} contracts`} />
              <SummaryTile label="Instrument" value={activeInstrument.label} />
              <SummaryTile label="Bid / Ask" value={`${priceSummary.bid} / ${priceSummary.ask}`} />
              <SummaryTile label="Last / Spread" value={`${priceSummary.last} • ${priceSummary.spread}`} />
            </div>

            <div className="mt-3 rounded-xl border border-zinc-300 bg-white dark:border-zinc-800 dark:bg-zinc-950/40 px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400">
              Prices update in real time from the Project X market data feed. Submit orders from your connected account once you are
              satisfied with the setup.
            </div>
          </div>
        </div>

        <div className="rounded-2xl border border-emerald-900/70 bg-emerald-950/40 p-4">
          <MarketDataTicker
            symbol={activeInstrument.mdSymbol}
            label={activeInstrument.label}
            onQuote={(quote) => setLatestQuote(quote)}
          />
        </div>

        <div className="rounded-2xl border border-amber-900/70 bg-amber-950/40 p-5 lg:col-span-3">
          <div className="space-y-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-amber-100">{liquiditySweepsPlaybook.headline}</div>
                <div className="mt-1 text-xs text-amber-50/80">{liquiditySweepsPlaybook.intro}</div>
              </div>
              <div className="rounded-full border border-amber-700/70 bg-amber-900/60 px-3 py-1 text-[11px] font-semibold text-amber-100">
                Sweep + reclaim
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {liquiditySweepsPlaybook.timeframes.map((tf) => (
                <div
                  key={tf.label}
                  className="rounded-lg border border-amber-900/70 bg-amber-950/60 px-3 py-2 text-xs text-amber-50/90"
                >
                  <div className="text-[11px] uppercase tracking-wide text-amber-200/70">{tf.label} bars</div>
                  <div className="mt-1 font-mono text-amber-100">
                    unit: {tf.unit} • unitNumber: {tf.unitNumber}
                  </div>
                </div>
              ))}
            </div>

            <div className="space-y-3">
              {liquiditySweepsPlaybook.steps.map((step) => (
                <div key={step.title} className="rounded-lg border border-amber-900/70 bg-amber-950/50 px-3 py-2">
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-amber-200/80">{step.title}</div>
                  <div className="text-xs text-amber-50/80">{step.description}</div>
                </div>
              ))}
            </div>

            <div className="rounded-lg border border-emerald-900/60 bg-emerald-950/50 px-3 py-2 text-xs text-emerald-50/80">
              Tip: keep <code>includePartialBar</code> set to <code>false</code>, sort bars oldest-to-newest, and require a tiny
              buffer past the prior swing (e.g., a few ticks) so a one-tick poke does not count as a real sweep.
            </div>
          </div>
        </div>

        <div className="rounded-2xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900/40 p-5 lg:col-span-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Strategy endpoint: Retrieve Bars</div>
              <div className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">
                Use the history service to pull aggregated bars for your strategy inputs.
              </div>
            </div>
            <div className="rounded-full border border-emerald-900 bg-emerald-950/60 px-3 py-1 text-[11px] font-semibold text-emerald-100">
              POST /api/History/retrieveBars
            </div>
          </div>

          <div className="mt-4 rounded-xl border border-amber-900 bg-amber-950/40 px-3 py-2 text-xs text-amber-100">
            Note: the API caps responses at 20,000 bars per request. Use <code>unit</code> and <code>unitNumber</code> to
            adjust granularity.
          </div>

          <div className="mt-4 overflow-auto rounded-xl border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/30">
            <table className="min-w-full text-left text-xs text-zinc-800 dark:text-zinc-200">
              <thead>
                <tr className="border-b border-zinc-800 bg-zinc-950/40">
                  <th className="px-3 py-2 font-semibold text-zinc-700 dark:text-zinc-300">Parameter</th>
                  <th className="px-3 py-2 font-semibold text-zinc-700 dark:text-zinc-300">Type</th>
                  <th className="px-3 py-2 font-semibold text-zinc-700 dark:text-zinc-300">Description</th>
                </tr>
              </thead>
              <tbody>
                {retrieveBarsParameters.map((param) => (
                  <tr key={param.name} className="border-b border-zinc-800 last:border-b-0">
                    <td className="px-3 py-2 font-semibold text-zinc-900 dark:text-zinc-100">{param.name}</td>
                    <td className="px-3 py-2 text-zinc-700 dark:text-zinc-300">{param.type}</td>
                    <td className="px-3 py-2 text-zinc-600 dark:text-zinc-400">{param.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
            <div className="space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-zinc-700 dark:text-zinc-300">Example request</div>
              <pre className="overflow-auto rounded-xl border border-zinc-800 bg-zinc-950/70 p-3 text-[11px] text-zinc-800 dark:text-zinc-200">
                <code>{retrieveBarsCurl}</code>
              </pre>
            </div>

            <div className="space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-zinc-700 dark:text-zinc-300">Example response</div>
              <pre className="overflow-auto rounded-xl border border-zinc-800 bg-zinc-950/70 p-3 text-[11px] text-zinc-800 dark:text-zinc-200">
                <code>{retrieveBarsResponse}</code>
              </pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function SummaryTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-300 bg-white dark:border-zinc-800 dark:bg-zinc-950/40 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-zinc-600 dark:text-zinc-400">{label}</div>
      <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{value}</div>
    </div>
  );
}
