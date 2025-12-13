import { fmtMoney } from "../../lib/format";
import { fmtDays, fmtDuration, fmtPF, fmtPct } from "../utils/format";
import type { DashboardComputed } from "../data/computeDashboard";

export type DaySummary = {
  activeDays: number;
  greenDays: number;
  redDays: number;
  flatDays: number;
  bestDay: { date: string; netPnl: number } | null;
  worstDay: { date: string; netPnl: number } | null;
};

interface SummaryCardsProps {
  totals: DashboardComputed["totals"] | undefined;
  daySummary: DaySummary;
  effectiveDaysBack: number;
}

export default function SummaryCards({ totals, daySummary, effectiveDaysBack }: SummaryCardsProps) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Net PnL</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtMoney(totals?.netPnl ?? 0)}</div>
          <div className="mt-1 text-xs text-zinc-500">Range: last {effectiveDaysBack} day(s)</div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Fees</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtMoney(totals?.fees ?? 0)}</div>
          <div className="mt-1 text-xs text-zinc-500">Gross: {fmtMoney(totals?.grossPnl ?? 0)}</div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Win rate</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtPct(totals?.winRate ?? 0)}</div>
          <div className="mt-1 text-xs text-zinc-500">W {totals?.wins ?? 0} / L {totals?.losses ?? 0} / BE {totals?.breakeven ?? 0}</div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Max drawdown</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtMoney(totals?.maxDrawdown ?? 0)}</div>
          <div className="mt-1 text-xs text-zinc-500">From daily equity</div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Profit factor</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtPF(totals?.profitFactor ?? 0)}</div>
          <div className="mt-1 text-xs text-zinc-500">Avg win {fmtMoney(totals?.avgWin ?? 0)} | Avg loss {fmtMoney(totals?.avgLoss ?? 0)}</div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Trades</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{totals?.realizedTrades ?? 0}</div>
          <div className="mt-1 text-xs text-zinc-500">Half-turns {totals?.halfTurns ?? 0} | Executions {totals?.totalExecutions ?? 0}</div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Day win rate</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtPct(totals?.dayWinRate ?? 0)}</div>
          <div className="mt-1 text-xs text-zinc-500">Green {daySummary.greenDays} | Red {daySummary.redDays} | Flat {daySummary.flatDays}</div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Avg trades/day</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{(totals?.avgTradesPerDay ?? 0).toFixed(2)}</div>
          <div className="mt-1 text-xs text-zinc-500">Active days {daySummary.activeDays}</div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Expectancy / trade</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtMoney(totals?.expectancyPerTrade ?? 0)}</div>
          <div className="mt-1 text-xs text-zinc-500">Tail risk (avg worst 5%): {fmtMoney(totals?.tailRiskAvg ?? 0)}</div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Risk & drawdown</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtMoney(totals?.maxIntradayDrawdown ?? 0)}</div>
          <div className="mt-1 text-xs text-zinc-500">Avg DD {fmtMoney(totals?.avgDrawdown ?? 0)} | Max length {fmtDays(totals?.maxDrawdownLengthDays ?? 0)}</div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Recovery</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtDays(totals?.avgTimeToRecoveryDays ?? 0)}</div>
          <div className="mt-1 text-xs text-zinc-500">Avg length {fmtDays(totals?.avgDrawdownLengthDays ?? 0)}</div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Efficiency</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtMoney(totals?.profitPerHour ?? 0)} / hr</div>
          <div className="mt-1 text-xs text-zinc-500">Per day {fmtMoney(totals?.profitPerDay ?? 0)}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Avg hold time</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtDuration(totals?.avgTradeDurationMs ?? 0)}</div>
          <div className="mt-1 text-xs text-zinc-500">All realized trades</div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Avg winner hold</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtDuration(totals?.avgWinDurationMs ?? 0)}</div>
          <div className="mt-1 text-xs text-zinc-500">Closed in profit</div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-xs text-zinc-400">Avg loser hold</div>
          <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtDuration(totals?.avgLossDurationMs ?? 0)}</div>
          <div className="mt-1 text-xs text-zinc-500">Closed in loss</div>
        </div>
      </div>
    </div>
  );
}
