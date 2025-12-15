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

const strategyOptions = [
  { value: "momentum", label: "Momentum" },
  { value: "mean-reversion", label: "Mean Reversion" },
  { value: "breakout", label: "Breakout" },
  { value: "scalping", label: "Scalping" },
];

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
      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-lg font-semibold text-zinc-100">Trade</div>
            <div className="mt-1 text-sm text-zinc-400">
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
          <div className="mt-3 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-sm text-zinc-200">
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
          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-sm font-semibold text-zinc-100">Order ticket</div>
                <div className="text-xs text-zinc-400">Pick the essentials to route an order.</div>
              </div>
              <div className="rounded-full border border-emerald-800 bg-emerald-900/40 px-3 py-1 text-[11px] font-semibold text-emerald-100">
                Live price: {priceSummary.reference !== null ? formatPrice(priceSummary.reference) : "--"}
              </div>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
              <label className="grid gap-1 text-sm">
                <span className="text-xs text-zinc-400">Account</span>
                <select
                  className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
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
                <span className="text-xs text-zinc-400">Strategy</span>
                <select
                  className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
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
                <span className="text-xs text-zinc-400">Instrument</span>
                <select
                  className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100"
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
                <span className="text-xs text-zinc-400">Contract size</span>
                <div className="flex items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2">
                  <input
                    type="range"
                    min={1}
                    max={10}
                    value={contractSize}
                    onChange={(e) => setContractSize(Number(e.target.value))}
                    className="h-2 w-full accent-emerald-400"
                  />
                  <span className="w-12 text-center text-sm text-zinc-100">{contractSize}x</span>
                </div>
              </label>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 rounded-xl border border-zinc-800 bg-zinc-950/30 p-3 sm:grid-cols-3">
              <SummaryTile label="Selected account" value={selectedAccountId ? `#${selectedAccountId}` : "None"} />
              <SummaryTile label="Strategy" value={strategyOptions.find((s) => s.value === selectedStrategy)?.label ?? "--"} />
              <SummaryTile label="Size" value={`${contractSize} contracts`} />
              <SummaryTile label="Instrument" value={activeInstrument.label} />
              <SummaryTile label="Bid / Ask" value={`${priceSummary.bid} / ${priceSummary.ask}`} />
              <SummaryTile label="Last / Spread" value={`${priceSummary.last} • ${priceSummary.spread}`} />
            </div>

            <div className="mt-3 rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-xs text-zinc-400">
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
      </div>
    </div>
  );
}

function SummaryTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/40 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-zinc-400">{label}</div>
      <div className="text-sm font-semibold text-zinc-100">{value}</div>
    </div>
  );
}
