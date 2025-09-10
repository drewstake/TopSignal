import Card from "../../components/ui/Card";
import { fmtUSD } from "../../lib/format";

export default function PnlCard({ unrealized = 0, realized = 0, loading }) {
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold tracking-wide">P&L</h2>
        {loading && <span className="text-xs text-zinc-400">Loading…</span>}
      </div>
      <div className="text-sm space-y-1 text-zinc-400">
        <div>Unrealized: {fmtUSD(unrealized)}</div>
        <div>Realized (today): {fmtUSD(realized)}</div>
      </div>
    </Card>
  );
}
