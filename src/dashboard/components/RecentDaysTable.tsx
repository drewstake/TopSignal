import { fmtMoney } from "../../lib/format";
import type { DashboardComputed } from "../data/computeDashboard";

interface RecentDaysTableProps {
  loading: boolean;
  computed: DashboardComputed | null;
}

export default function RecentDaysTable({ loading, computed }: RecentDaysTableProps) {
  return (
    <div className="mt-4 overflow-hidden rounded-2xl border border-zinc-800">
      <div className="grid grid-cols-12 bg-zinc-950/40 px-4 py-2 text-xs text-zinc-400">
        <div className="col-span-3">Date</div>
        <div className="col-span-3">Net</div>
        <div className="col-span-2">Trades</div>
        <div className="col-span-2">Contracts</div>
        <div className="col-span-2">Fees</div>
      </div>

      <div className="divide-y divide-zinc-800 bg-zinc-950/20">
        {loading ? (
          <div className="px-4 py-4 text-sm text-zinc-300">Loading...</div>
        ) : !computed || computed.days.length === 0 ? (
          <div className="px-4 py-4 text-sm text-zinc-300">No day data found for this range.</div>
        ) : (
          computed.days
            .slice()
            .reverse()
            .slice(0, 25)
            .map((d) => (
              <div key={d.date} className="grid grid-cols-12 items-center px-4 py-3 text-sm text-zinc-200">
                <div className="col-span-3 text-zinc-300">{d.date}</div>
                <div className="col-span-3">{fmtMoney(d.netPnl)}</div>
                <div className="col-span-2">{d.trades}</div>
                <div className="col-span-2">{d.contracts}</div>
                <div className="col-span-2">{fmtMoney(d.fees)}</div>
              </div>
            ))
        )}
      </div>
    </div>
  );
}
