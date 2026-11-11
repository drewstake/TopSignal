import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "../../components/ui/Button";
import { accountsApi } from "../../lib/api";
import type { AccountTrade } from "../../lib/types";
import { ProgressRecorderStatus, TradeProgressReview } from "../trades/TradeProgress";
import { usePageVisibility } from "./usePageVisibility";

const POLL_MS = 30_000;

export function TopBotTradeRecorder({ accountId }: { accountId: number }) {
  return <AccountRecorder key={accountId} accountId={accountId} />;
}

function AccountRecorder({ accountId }: { accountId: number }) {
  const [expanded, setExpanded] = useState(false);
  const [trades, setTrades] = useState<AccountTrade[] | null>(null);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  const [reviewTrade, setReviewTrade] = useState<AccountTrade | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const generation = useRef(0);
  const visible = usePageVisibility();
  useEffect(() => {
    // Bounded refresh while the list is open and visible. The shared trade cache
    // cannot abort an in-flight read, so superseded responses are discarded.
    if (!expanded || !visible) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const current = ++generation.current;
    const load = async () => {
      try {
        const result = await accountsApi.getTrades(accountId, { limit: 20 }, { bypassCache: true });
        if (stopped || current !== generation.current) return;
        setTrades(result.filter(trade => trade.account_id === accountId));
        setError(false);
        setUpdatedAt(Date.now());
      } catch {
        if (!stopped && current === generation.current) setError(true);
      } finally {
        if (!stopped && current === generation.current) timer = setTimeout(load, POLL_MS);
      }
    };
    void load();
    return () => { stopped = true; clearTimeout(timer); };
  }, [accountId, expanded, revision, visible]);

  return <section className="topbot-panel" aria-label="Trade recorder">
    <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
      <div>
        <h2 className="text-sm font-semibold">Trade recorder · 1 second</h2>
        <p className="mt-1 text-xs text-app-muted">When enabled, records points up or down during your TopBot and manual trades, even with this page closed.</p>
      </div>
      <Button size="sm" variant="ghost" aria-expanded={expanded} aria-controls="topbot-recorded-trades"
        onClick={() => { setExpanded(value => !value); setTrades(null); setError(false); }}>
        {expanded ? "Hide trade paths" : "Review trade paths"}
      </Button>
    </div>
    <ProgressRecorderStatus account={accountId} />
    {expanded && <div id="topbot-recorded-trades" className="mt-4 space-y-3">
      <div className="flex flex-wrap justify-between gap-2 text-xs text-app-muted">
        <p>Recent trades · select a trade to see its recorded points over time.</p>
        <Link to={`/trades?account=${accountId}`} className="text-app-accent hover:underline">All trades</Link>
      </div>
      {error && trades !== null && <p role="status" className="text-xs text-amber-200">Refresh failed. Showing trades from {updatedAt ? new Date(updatedAt).toLocaleTimeString("en-US", { timeZone: "America/New_York" }) : "the last successful read"} ET.</p>}
      {error && trades === null ? <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-amber-200">
        <p>Recent trades are unavailable.</p>
        <Button size="sm" variant="ghost" onClick={() => { setError(false); setRevision(value => value + 1); }}>Retry</Button>
      </div> : trades === null ? <p role="status" className="text-sm text-app-muted">Loading recent trades…</p>
        : trades.length === 0 ? <p className="text-sm text-app-muted">No recent trades for this account. Recorded paths become available as trades appear here.</p>
        : <ul className="divide-y divide-app-border">
          {trades.map(trade => <li key={trade.id} className="flex flex-wrap items-center justify-between gap-3 py-2 text-xs">
            <div><strong>{trade.symbol}</strong> · {trade.side} · {trade.size} contract{trade.size === 1 ? "" : "s"}
              <p className="mt-1 text-app-muted">{new Date(trade.timestamp).toLocaleString("en-US", {
                timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
              })} ET</p>
            </div>
            <Button size="sm" variant="ghost" aria-label={`Review path for ${trade.symbol} trade ${trade.id}`}
              onClick={() => setReviewTrade(trade)}>Review path</Button>
          </li>)}
        </ul>}
      <p className="text-xs text-app-muted">Refreshes every 30 s while open{updatedAt ? ` · updated ${new Date(updatedAt).toLocaleTimeString("en-US", { timeZone: "America/New_York" })} ET` : ""}. Trades from before recording was enabled may have no path. Missing observations are shown as gaps. These paths cover each position from entry to exit only; post-exit market data for the exit study is recorded separately.</p>
    </div>}
    {reviewTrade && <TradeProgressReview key={reviewTrade.id} trade={reviewTrade} onClose={() => setReviewTrade(null)} />}
  </section>;
}
