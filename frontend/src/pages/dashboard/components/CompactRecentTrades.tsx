import { memo } from "react";

import { cn } from "../../../components/ui/cn";
import { formatTradeDirection } from "../../../lib/tradeDirection";
import { getTradeNetPnl } from "../../../lib/tradePnl";
import { getDisplayTradeSymbol } from "../../../lib/tradeSymbol";
import type { AccountTrade } from "../../../lib/types";
import { formatPnl } from "../../../utils/formatters";
import { CompactPanel, CompactState } from "./CompactDashboardPrimitives";

const tradeDateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "America/New_York",
});

function pnlTextClass(value: number | null) {
  if (value === null || value === 0) {
    return "text-app-text";
  }
  return value > 0 ? "text-app-positive-text" : "text-app-negative-text";
}

function directionClass(direction: string) {
  if (direction === "LONG") {
    return "bg-app-accent/10 text-app-text";
  }
  if (direction === "SHORT") {
    return "bg-app-warning/10 text-app-text";
  }
  return "bg-app-surface-raised text-app-muted-text";
}

function formatTradeDate(trade: AccountTrade) {
  const date = new Date(trade.exit_time ?? trade.timestamp);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : tradeDateFormatter.format(date);
}

function TradePnl({ value }: { value: number | null }) {
  return (
    <span className={cn("whitespace-nowrap font-semibold tabular-nums", pnlTextClass(value))}>
      {value === null ? "Not available" : formatPnl(value)}
    </span>
  );
}

export const CompactRecentTrades = memo(function CompactRecentTrades({
  trades,
  loading,
  error,
  accountNameById,
}: {
  trades: readonly AccountTrade[];
  loading: boolean;
  error: string | null;
  accountNameById?: Readonly<Record<number, string>>;
}) {
  const visibleTrades = trades.slice(0, 7);
  const showAccount = Boolean(accountNameById);

  return (
    <CompactPanel
      title="Recent Trades"
      info="The seven most recently closed positions in the current dashboard scope. Direction is inferred from the closing action: SELL means Long and BUY means Short."
      className="h-full"
    >
      {loading ? (
        <CompactState kind="loading" title="Loading recent trades" minHeightClassName="min-h-[300px]" />
      ) : error ? (
        <CompactState kind="error" title="Recent trades unavailable" detail={error} minHeightClassName="min-h-[300px]" />
      ) : visibleTrades.length === 0 ? (
        <CompactState
          kind="empty"
          title="No recent trades"
          detail="No closed positions fall inside this dashboard scope."
          minHeightClassName="min-h-[300px]"
        />
      ) : (
        <>
          <ul className="divide-y divide-app-border/60 px-3 sm:hidden" aria-label="Recent closed positions">
            {visibleTrades.map((trade) => {
              const direction = formatTradeDirection(trade.side);
              const accountName = accountNameById?.[trade.account_id];
              const metadata = `${formatTradeDate(trade)} ET${accountName ? ` · ${accountName}` : ""}`;
              return (
                <li key={`${trade.account_id}:${trade.id}`} className="py-3">
                  <div className="flex min-w-0 items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-app-text">
                          {getDisplayTradeSymbol(trade.symbol, trade.contract_id)}
                        </span>
                        <span className={cn("rounded-full px-2 py-1 text-xs font-semibold", directionClass(direction))}>
                          {direction}
                        </span>
                      </div>
                      <p className="mt-1 truncate text-xs text-app-muted-text" title={metadata}>
                        {metadata}
                      </p>
                    </div>
                    <TradePnl value={getTradeNetPnl(trade)} />
                  </div>
                </li>
              );
            })}
          </ul>

          <div className="hidden p-3 sm:block">
            <table className="w-full table-fixed text-left text-xs">
              <caption className="sr-only">Seven most recently closed positions</caption>
              <thead>
                <tr className="bg-app-surface-raised/60 text-app-muted-text">
                  <th className="w-[26%] rounded-l-lg px-2 py-3 font-medium" scope="col">Close (ET)</th>
                  {showAccount ? <th className="w-[20%] px-2 py-3 font-medium" scope="col">Account</th> : null}
                  <th className="px-2 py-3 font-medium" scope="col">Symbol</th>
                  <th className="w-[15%] px-2 py-3 font-medium" scope="col">Side</th>
                  <th className="w-[22%] rounded-r-lg px-2 py-3 text-right font-medium" scope="col">Net P&amp;L</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-app-border/60">
                {visibleTrades.map((trade) => {
                  const direction = formatTradeDirection(trade.side);
                  return (
                    <tr key={`${trade.account_id}:${trade.id}`} className="transition hover:bg-app-surface-raised/35">
                      <td className="truncate px-2 py-3 text-app-muted-text" title={formatTradeDate(trade)}>{formatTradeDate(trade)}</td>
                      {showAccount ? (
                        <td className="truncate px-2 py-3 text-app-muted-text" title={accountNameById?.[trade.account_id] ?? "Unknown account"}>
                          {accountNameById?.[trade.account_id] ?? "Unknown"}
                        </td>
                      ) : null}
                      <td className="truncate px-2 py-3 font-medium text-app-text">
                        {getDisplayTradeSymbol(trade.symbol, trade.contract_id)}
                      </td>
                      <td className="px-2 py-3 text-app-text">{direction}</td>
                      <td className="truncate px-2 py-3 text-right"><TradePnl value={getTradeNetPnl(trade)} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </CompactPanel>
  );
});
