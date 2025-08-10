import { Activity } from "lucide-react";
import Card from "../../components/ui/Card";

export default function LiveMarketCard({ price, delta, deltaPct }) {
  return (
    <Card className="p-6 relative overflow-hidden">
      <div className="absolute -right-10 -top-10 h-40 w-40 rounded-full bg-gradient-to-br from-indigo-500/30 to-fuchsia-600/30 blur-2xl" />
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs uppercase tracking-widest text-zinc-400">Live Market</p>
          <h3 className="text-2xl font-semibold mt-1">NQ • Nasdaq 100 Futures</h3>
          <div className="mt-4 flex items-baseline gap-4">
            <span className="text-5xl font-bold tabular-nums">{price != null ? price.toFixed(2) : "--"}</span>
            <span className={`text-lg font-medium ${Number(delta) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
              {Number(delta) >= 0 ? "+" : ""}
              {Number(delta || 0).toFixed(2)} ({Number(deltaPct || 0).toFixed(2)}%)
            </span>
          </div>
          <p className="mt-2 text-xs text-zinc-400">Real-time via SignalR (trade ticks flush immediately)</p>
        </div>
        <Activity className="h-10 w-10 text-indigo-400" />
      </div>
    </Card>
  );
}
