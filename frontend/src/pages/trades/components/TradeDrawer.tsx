import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Drawer } from "../../../components/ui/Drawer";
import type { Trade } from "../../../mock/data";

export interface TradeDrawerProps {
  trade: Trade | null;
  open: boolean;
  onClose: () => void;
}

export function TradeDrawer({ trade, open, onClose }: TradeDrawerProps) {
  if (!trade) {
    return (
      <Drawer open={open} onClose={onClose} title="Trade Detail">
        <p className="text-sm text-slate-400">Select a trade row to view details.</p>
      </Drawer>
    );
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={`${trade.symbol} ${trade.side}`}
      description={`${trade.strategy} | ${trade.session}`}
      footer={
        <div className="flex items-center justify-between">
          <Badge variant={trade.ruleBreached ? "negative" : "positive"}>
            {trade.ruleBreached ? "Rule breach" : "Within plan"}
          </Badge>
          <Button variant="secondary" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Trade ID" value={trade.id} />
          <Field label="Setup Quality" value={`${trade.setupQuality}/10`} />
          <Field label="Entry" value={`$${trade.entry.toFixed(2)}`} />
          <Field label="Exit" value={`$${trade.exit.toFixed(2)}`} />
          <Field label="Quantity" value={`${trade.quantity}`} />
          <Field
            label="PnL"
            value={`${trade.pnl >= 0 ? "+" : ""}$${trade.pnl.toFixed(2)}`}
            valueClassName={trade.pnl >= 0 ? "text-emerald-300" : "text-rose-300"}
          />
          <Field
            label="R Multiple"
            value={`${trade.riskMultiple >= 0 ? "+" : ""}${trade.riskMultiple.toFixed(1)}R`}
            valueClassName={trade.riskMultiple >= 0 ? "text-emerald-300" : "text-rose-300"}
          />
          <Field label="Session" value={trade.session} />
        </div>
        <div className="space-y-2 rounded-xl border border-slate-800 bg-slate-900/60 p-3">
          <p className="text-xs uppercase tracking-wide text-slate-500">Timestamps</p>
          <p className="text-sm text-slate-200">Opened: {trade.openedAt}</p>
          <p className="text-sm text-slate-200">Closed: {trade.closedAt}</p>
        </div>
        <div className="space-y-2 rounded-xl border border-slate-800 bg-slate-900/60 p-3">
          <p className="text-xs uppercase tracking-wide text-slate-500">Notes</p>
          <p className="text-sm text-slate-300">{trade.notes}</p>
        </div>
      </div>
    </Drawer>
  );
}

interface FieldProps {
  label: string;
  value: string;
  valueClassName?: string;
}

function Field({ label, value, valueClassName }: FieldProps) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-sm font-medium text-slate-100 ${valueClassName ?? ""}`}>{value}</p>
    </div>
  );
}
