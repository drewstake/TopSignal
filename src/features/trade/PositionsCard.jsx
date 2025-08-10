import Card from "../../components/ui/Card";
import Button from "../../components/ui/Button";
import Label from "../../components/ui/Label";

export default function PositionsCard({ positions, onClose, onPartial, loading }) {
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold tracking-wide">Open Positions</h2>
        <span className="text-xs text-zinc-400">{loading ? "Loading…" : `${positions.length} open`}</span>
      </div>

      <div className="space-y-3">
        {positions.length === 0 && (
          <p className="text-sm text-zinc-400">No open positions.</p>
        )}

        {positions.map((p) => (
          <div key={p.id} className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 flex items-center justify-between">
            <div className="text-sm">
              <div className="font-medium">{p.contractId}</div>
              <div className="text-zinc-400">Size: {p.size} • Avg: {p.averagePrice != null ? p.averagePrice : "—"}</div>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="ghost" onClick={()=>onPartial(p.contractId, 1)} disabled={p.size <= 0}>Close 1</Button>
              <Button variant="danger" onClick={()=>onClose(p.contractId)}>Close All</Button>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
