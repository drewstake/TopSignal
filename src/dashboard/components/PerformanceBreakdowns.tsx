import DayOfWeekBarChart from "./charts/DayOfWeekBarChart";
import EquityCurveChart from "./charts/EquityCurveChart";
import TradeTimeHistogram from "./charts/TradeTimeHistogram";
import { fmtMoney } from "../../lib/format";
import type { DashboardComputed } from "../data/computeDashboard";
import type { EquityPoint } from "../../types/metrics";

interface TimeAnalysis {
  timeData: { label: string; trades: number; netPnl: number }[];
  dayData: { label: string; netPnl: number; trades: number }[];
  busiestTime: { label: string; trades: number; netPnl: number } | null;
  bestTime: { label: string; trades: number; netPnl: number } | null;
  bestDay: { label: string; netPnl: number; trades: number } | null;
}

interface PerformanceBreakdownsProps {
  loading: boolean;
  timeAnalysis: TimeAnalysis;
  totals: DashboardComputed["totals"] | undefined;
  equity: EquityPoint[];
  effectiveDaysBack: number;
}

export default function PerformanceBreakdowns({ loading, timeAnalysis, totals, equity, effectiveDaysBack }: PerformanceBreakdownsProps) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="text-sm font-semibold text-zinc-100">Time-of-day performance</div>
          <div className="text-xs text-zinc-500">Session ranges shown in New York time.</div>
          <div className="mt-2 divide-y divide-zinc-800 text-sm text-zinc-200">
            {(totals?.timeBlocks || []).map((b) => (
              <div key={b.label} className="flex items-center justify-between py-2">
                <div>{b.label}</div>
                <div className="text-right">
                  <div>{fmtMoney(b.netPnl)}</div>
                  <div className="text-xs text-zinc-500">Trades {b.trades}</div>
                </div>
              </div>
            ))}
            {!totals?.timeBlocks?.length ? <div className="py-2 text-zinc-400">No realized trades in range.</div> : null}
          </div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="mb-2 flex items-center justify-between text-sm text-zinc-100">
            <div>
              <div className="font-semibold">Trade timing</div>
              <div className="text-xs text-zinc-400">Precise trade times & PnL (New York time)</div>
            </div>
            <div className="text-xs text-zinc-400">{timeAnalysis.busiestTime ? `${timeAnalysis.busiestTime.trades} trades` : "--"}</div>
          </div>

          {loading ? (
            <div className="py-6 text-sm text-zinc-300">Loading...</div>
          ) : !timeAnalysis.timeData.length ? (
            <div className="py-6 text-sm text-zinc-300">No realized trades to chart.</div>
          ) : (
            <TradeTimeHistogram data={timeAnalysis.timeData} />
          )}

          {timeAnalysis.bestTime ? (
            <div className="mt-2 text-xs text-emerald-300">
              Most profitable time: {timeAnalysis.bestTime.label} ({fmtMoney(timeAnalysis.bestTime.netPnl)}; {timeAnalysis.bestTime.trades}
              trade{timeAnalysis.bestTime.trades === 1 ? "" : "s"}).
            </div>
          ) : null}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="mb-2 flex items-center justify-between text-sm text-zinc-100">
            <div>
              <div className="font-semibold">Day-of-week performance</div>
              <div className="text-xs text-zinc-400">Net PnL by session day</div>
            </div>
            <div className="text-xs text-zinc-400">{timeAnalysis.bestDay ? `${timeAnalysis.bestDay.trades} trades` : "--"}</div>
          </div>

          {loading ? (
            <div className="py-6 text-sm text-zinc-300">Loading...</div>
          ) : !timeAnalysis.dayData.some((d) => d.trades > 0) ? (
            <div className="py-6 text-sm text-zinc-300">No trading days to show.</div>
          ) : (
            <DayOfWeekBarChart data={timeAnalysis.dayData} />
          )}

          {timeAnalysis.bestDay ? (
            <div className="mt-2 text-xs text-emerald-300">Most profitable day: {timeAnalysis.bestDay.label} ({fmtMoney(timeAnalysis.bestDay.netPnl)}).</div>
          ) : null}
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="mb-2 flex items-center justify-between text-sm text-zinc-100">
            <div>
              <div className="font-semibold">Instrument breakdown</div>
              <div className="text-xs text-zinc-400">PnL by contract</div>
            </div>
            <div className="text-xs text-zinc-400">{totals?.instruments.length ?? 0} instrument(s)</div>
          </div>
          <div className="mt-2 divide-y divide-zinc-800 text-sm text-zinc-200">
            {(totals?.instruments || []).map((i) => (
              <div key={i.contractId} className="flex items-center justify-between py-2">
                <div className="text-xs text-zinc-400">{i.contractId}</div>
                <div className="text-right">
                  <div>{fmtMoney(i.netPnl)}</div>
                  <div className="text-xs text-zinc-500">Trades {i.trades}</div>
                </div>
              </div>
            ))}
            {!totals?.instruments?.length ? <div className="py-2 text-zinc-400">No realized trades in range.</div> : null}
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
        <div className="mb-2 flex items-center justify-between text-sm text-zinc-100">
          <div>
            <div className="font-semibold">Equity curve</div>
            <div className="text-xs text-zinc-400">Cumulative net PnL with daily PnL overlay</div>
          </div>
          <div className="text-xs text-zinc-400">Range: last {effectiveDaysBack} day(s)</div>
        </div>

        {loading ? (
          <div className="py-6 text-sm text-zinc-300">Loading...</div>
        ) : !equity.length ? (
          <div className="py-6 text-sm text-zinc-300">No equity data found for this range.</div>
        ) : (
          <EquityCurveChart data={equity} />
        )}
      </div>
    </div>
  );
}
