import { memo, useMemo } from "react";

import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { getDemoTradeId } from "../../../lib/demoMode";
import { formatTradeDirection, tradeDirectionBadgeVariant } from "../../../lib/tradeDirection";
import { getTradeNetPnl } from "../../../lib/tradePnl";
import { getDisplayTradeSymbol } from "../../../lib/tradeSymbol";
import type { AccountTrade } from "../../../lib/types";
import { formatInteger, formatPnl } from "../../../utils/formatters";
import { RECENT_TRADES_RENDER_PAGE_SIZE, getVisibleRecentTrades } from "../recentTradesPagination";

const timestampFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: true,
  timeZone: "America/New_York",
});

function formatTradeDuration(minutes: number | null | undefined) {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) {
    return "-";
  }

  const safeMinutes = Math.max(0, minutes);
  const totalSeconds = Math.round(safeMinutes * 60);
  const totalMinutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const hours = Math.floor(totalMinutes / 60);
  const minutesRemainder = totalMinutes % 60;

  if (hours > 0) {
    return `${hours}h ${minutesRemainder}m`;
  }
  return `${minutesRemainder}m ${seconds}s`;
}

function pnlClass(value: number) {
  return value >= 0 ? "text-app-positive" : "text-app-negative";
}

interface RecentTradesCardProps {
  trades: readonly AccountTrade[];
  loading: boolean;
  error: string | null;
  selectedTradeDate: string | null;
  selectedTradeDateLabel: string | null;
  visibleCount: number;
  recentTradeLimit: number;
  dayFilterTradeLimit: number;
  onClearDayFilter: () => void;
  onShowMore: () => void;
}

export const RecentTradesCard = memo(function RecentTradesCard({
  trades,
  loading,
  error,
  selectedTradeDate,
  selectedTradeDateLabel,
  visibleCount,
  recentTradeLimit,
  dayFilterTradeLimit,
  onClearDayFilter,
  onShowMore,
}: RecentTradesCardProps) {
  const visibleTrades = useMemo(() => getVisibleRecentTrades(trades, visibleCount), [trades, visibleCount]);
  const hasMoreTrades = visibleTrades.length < trades.length;

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle>{selectedTradeDate ? "Trade Events" : "Recent Trade Events"}</CardTitle>
          <CardDescription>
            {selectedTradeDate
              ? `Showing trades for ${selectedTradeDateLabel ?? selectedTradeDate}, up to ${dayFilterTradeLimit} events.`
              : `Showing up to ${recentTradeLimit} most recent events for the active account.`}
          </CardDescription>
        </div>
        {selectedTradeDate ? (
          <Button variant="ghost" size="sm" onClick={onClearDayFilter}>
            Clear Day Filter
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-0">
        <div className="max-h-[320px] overflow-auto rounded-xl border border-app-border/80">
          <table className="w-full min-w-[1100px] table-fixed border-collapse text-sm whitespace-nowrap">
            <thead className="sticky top-0 z-10 bg-app-surface/95 text-xs uppercase tracking-wide text-app-muted">
              <tr>
                <th className="w-[10%] px-2 py-2 text-left font-medium">Entry Time (ET)</th>
                <th className="w-[10%] px-2 py-2 text-left font-medium">Exit Time (ET)</th>
                <th className="w-[10%] px-2 py-2 text-center font-medium">Duration</th>
                <th className="w-[10%] px-2 py-2 text-center font-medium">Symbol</th>
                <th className="w-[10%] px-2 py-2 text-center font-medium">Direction</th>
                <th className="w-[10%] px-2 py-2 text-center font-medium">Size</th>
                <th className="w-[10%] px-2 py-2 text-right font-medium">Entry Price</th>
                <th className="w-[10%] px-2 py-2 text-right font-medium">Exit Price</th>
                <th className="w-[10%] px-2 py-2 text-right font-medium">PnL</th>
                <th className="w-[10%] px-2 py-2 text-right font-medium">Trade ID</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-app-border/70">
              {loading ? (
                <tr>
                  <td colSpan={10} className="px-2 py-4 text-center text-app-muted">
                    Loading trades...
                  </td>
                </tr>
              ) : error ? (
                <tr>
                  <td colSpan={10} className="px-2 py-4 text-center text-app-negative">
                    {error}
                  </td>
                </tr>
              ) : trades.length === 0 ? (
                <tr>
                  <td colSpan={10} className="px-2 py-4 text-center text-app-muted">
                    No trades available.
                  </td>
                </tr>
              ) : (
                visibleTrades.map((trade) => {
                  const pnlValue = getTradeNetPnl(trade) ?? 0;
                  const direction = formatTradeDirection(trade.side);
                  const entryTime = trade.entry_time;
                  const exitTime = trade.exit_time ?? trade.timestamp;
                  const entryPrice = trade.entry_price;
                  const exitPrice = trade.exit_price ?? trade.price;
                  return (
                    <tr key={trade.id} className="transition hover:bg-app-surface/70">
                      <td className="px-2 py-2 text-left text-app-muted">
                        {entryTime ? timestampFormatter.format(new Date(entryTime)) : "-"}
                      </td>
                      <td className="px-2 py-2 text-left text-app-muted">
                        {timestampFormatter.format(new Date(exitTime))}
                      </td>
                      <td className="px-2 py-2 text-center text-app-muted">
                        {formatTradeDuration(trade.duration_minutes)}
                      </td>
                      <td className="px-2 py-2 text-center font-medium text-app-text">
                        {getDisplayTradeSymbol(trade.symbol, trade.contract_id)}
                      </td>
                      <td className="px-2 py-2 text-center">
                        <Badge variant={tradeDirectionBadgeVariant(trade.side)}>{direction}</Badge>
                      </td>
                      <td className="px-2 py-2 text-center text-app-text-soft">{formatInteger(trade.size)}</td>
                      <td className="px-2 py-2 text-right font-mono text-app-text-soft">
                        {entryPrice == null
                          ? "-"
                          : entryPrice.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 5 })}
                      </td>
                      <td className="px-2 py-2 text-right font-mono text-app-text-soft">
                        {exitPrice.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 5 })}
                      </td>
                      <td className={`px-2 py-2 text-right font-semibold ${pnlClass(pnlValue)}`}>
                        {formatPnl(pnlValue)}
                      </td>
                      <td className="px-2 py-2 text-right font-mono text-app-muted">
                        {getDemoTradeId(trade.source_trade_id ?? trade.order_id)}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        {!loading && !error && trades.length > 0 ? (
          <div className="flex flex-wrap items-center justify-between gap-2 px-1 pt-3 text-xs text-app-muted">
            <p aria-live="polite">
              {`Showing ${visibleTrades.length.toLocaleString("en-US")} of ${trades.length.toLocaleString("en-US")} trades`}
            </p>
            {hasMoreTrades ? (
              <Button type="button" variant="ghost" size="sm" onClick={onShowMore}>
                {`Show ${Math.min(RECENT_TRADES_RENDER_PAGE_SIZE, trades.length - visibleTrades.length)} more`}
              </Button>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
});
