import Card from "../../components/ui/Card";
import Button from "../../components/ui/Button";
import { timeStamp } from "../../lib/format";

export default function OrdersTable({ orders, onCancel, refreshing }) {
  return (
    <Card className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold tracking-wide">Open Orders</h3>
        <span className="text-xs text-zinc-400">
          {refreshing ? "Refreshing…" : `${orders.length} open`}
        </span>
      </div>
      <div className="overflow-auto max-h-[420px] rounded-xl border border-white/5">
        <table className="w-full text-sm">
          <thead className="bg-white/5 text-zinc-300 sticky top-0">
            <tr>
              <th className="text-left px-4 py-3 font-medium">Time</th>
              <th className="text-left px-4 py-3 font-medium">Contract</th>
              <th className="text-left px-4 py-3 font-medium">Type</th>
              <th className="text-left px-4 py-3 font-medium">Side</th>
              <th className="text-right px-4 py-3 font-medium">Qty</th>
              <th className="text-left px-4 py-3 font-medium">Status</th>
              <th className="text-right px-4 py-3 font-medium">Action</th>
            </tr>
          </thead>
          <tbody>
            {orders.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-zinc-400">
                  No open orders.
                </td>
              </tr>
            )}
            {orders.map((o) => (
              <tr key={o.id} className="border-t border-white/5 hover:bg-white/5 transition-colors">
                <td className="px-4 py-3 text-zinc-300">{timeStamp(o.createdAt)}</td>
                <td className="px-4 py-3">{o.contractId}</td>
                <td className="px-4 py-3">{o.type}</td>
                <td className={`px-4 py-3 ${o.sideCode === 0 ? "text-emerald-400" : "text-rose-400"}`}>{o.side}</td>
                <td className="px-4 py-3 text-right tabular-nums">{o.size}</td>
                <td className="px-4 py-3">
                  <span
                    className={`inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-medium border ${
                      o.status === "Working"
                        ? "border-amber-300/30 text-amber-300"
                        : o.status === "Filled"
                        ? "border-emerald-400/30 text-emerald-400"
                        : o.status === "Cancelled"
                        ? "border-rose-400/30 text-rose-400"
                        : "border-white/20 text-zinc-300"
                    }`}
                  >
                    {o.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <Button
                    variant="ghost"
                    disabled={o.status !== "Working"}
                    onClick={() => onCancel?.(o.id)}
                  >
                    Cancel
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
