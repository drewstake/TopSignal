import { useMemo, useState } from "react";
import Card from "../../components/ui/Card";
import Label from "../../components/ui/Label";
import Select from "../../components/ui/Select";
import Button from "../../components/ui/Button";
import { fmtUSD } from "../../lib/format";

const TYPE_OPTS = [
  { id: 2, name: "Market" },
  { id: 1, name: "Limit" },
  { id: 4, name: "Stop" },
  { id: 5, name: "TrailingStop" },
  { id: 6, name: "JoinBid" },
  { id: 7, name: "JoinAsk" },
];

export default function OrderTicket({
  accountId,
  contractId,           // e.g. VITE_NQ_CONTRACT_ID
  lastPrice,            // hint
  onPlace,              // async function({type, side, size, ...}) => orderId
  onPlaced,             // callback(orderId)
  disabled,
  log,                  // optional logger
}) {
  const [type, setType] = useState(2);
  const [side, setSide] = useState(0);     // 0=Buy, 1=Sell
  const [size, setSize] = useState(1);
  const [limitPrice, setLimitPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");
  const [trailPrice, setTrailPrice] = useState("");
  const [customTag, setCustomTag] = useState("");
  const [placing, setPlacing] = useState(false);

  const needsLimit = type === 1;
  const needsStop  = type === 4;
  const needsTrail = type === 5;

  const canSubmit = useMemo(() => {
    if (!accountId || !contractId) return false;
    if (!size || size <= 0) return false;
    if (type === 1 && !Number(limitPrice)) return false;
    if (type === 4 && !Number(stopPrice)) return false;
    if (type === 5 && !Number(trailPrice)) return false;
    return true;
  }, [accountId, contractId, type, size, limitPrice, stopPrice, trailPrice]);

  async function submit() {
    if (!canSubmit) return;
    setPlacing(true);
    try {
      const payload = {
        type,
        side,
        size: Number(size),
        limitPrice: needsLimit ? Number(limitPrice) : null,
        stopPrice:  needsStop  ? Number(stopPrice)  : null,
        trailPrice: needsTrail ? Number(trailPrice) : null,
        customTag: customTag || `TSX-${Date.now()}`,
      };
      const orderId = await onPlace({
        accountId: Number(accountId),
        contractId,
        ...payload,
      });
      onPlaced?.(orderId);
      log?.(`Order placed (${orderId}) ${side === 0 ? "Buy" : "Sell"} ${size} ${contractId} [${TYPE_OPTS.find(t=>t.id===type)?.name}]`);
      // quick reset for convenience (keep side/size)
      if (type !== 1) setLimitPrice("");
      if (type !== 4) setStopPrice("");
      if (type !== 5) setTrailPrice("");
    } catch (e) {
      alert(e?.message || "Place order failed");
      log?.(`Place order failed: ${e?.message || e}`);
    } finally {
      setPlacing(false);
    }
  }

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold tracking-wide">Order Ticket</h2>
        <span className="text-xs text-zinc-400">Acct #{accountId || "—"}</span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label>Type</Label>
          <Select value={type} onChange={(e)=>setType(Number(e.target.value))}>
            {TYPE_OPTS.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
          </Select>
        </div>

        <div>
          <Label>Side</Label>
          <div className="grid grid-cols-2 gap-2">
            <Button variant={side === 0 ? "primary" : "ghost"} onClick={()=>setSide(0)}>Buy</Button>
            <Button variant={side === 1 ? "danger"  : "ghost"} onClick={()=>setSide(1)}>Sell</Button>
          </div>
        </div>

        <div>
          <Label>Size</Label>
          <input
            inputMode="numeric"
            value={size}
            onChange={(e)=>setSize(Number(e.target.value))}
            className="w-full rounded-xl bg-zinc-900/60 border border-white/10 px-3 py-2 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
          />
        </div>

        <div>
          <Label>Last</Label>
          <div className="px-3 py-2 rounded-xl bg-white/5 border border-white/10 text-sm text-zinc-200">
            {lastPrice != null ? lastPrice.toFixed(2) : "—"}
          </div>
        </div>

        {needsLimit && (
          <div className="col-span-2">
            <Label>Limit Price</Label>
            <input
              value={limitPrice}
              onChange={(e)=>setLimitPrice(e.target.value)}
              placeholder={lastPrice ? (lastPrice + 1).toFixed(2) : ""}
              className="w-full rounded-xl bg-zinc-900/60 border border-white/10 px-3 py-2 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
            />
          </div>
        )}

        {needsStop && (
          <div className="col-span-2">
            <Label>Stop Price</Label>
            <input
              value={stopPrice}
              onChange={(e)=>setStopPrice(e.target.value)}
              placeholder={lastPrice ? (lastPrice - 5).toFixed(2) : ""}
              className="w-full rounded-xl bg-zinc-900/60 border border-white/10 px-3 py-2 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
            />
          </div>
        )}

        {needsTrail && (
          <div className="col-span-2">
            <Label>Trail Price</Label>
            <input
              value={trailPrice}
              onChange={(e)=>setTrailPrice(e.target.value)}
              placeholder="e.g. 5.0"
              className="w-full rounded-xl bg-zinc-900/60 border border-white/10 px-3 py-2 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
            />
          </div>
        )}

        <div className="col-span-2">
          <Label>Custom Tag (optional)</Label>
          <input
            value={customTag}
            onChange={(e)=>setCustomTag(e.target.value)}
            placeholder="Unique per account (auto-set if empty)"
            className="w-full rounded-xl bg-zinc-900/60 border border-white/10 px-3 py-2 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
          />
        </div>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <Button onClick={submit} disabled={placing || disabled || !canSubmit}>
          {placing ? "Placing…" : `Place ${side === 0 ? "Buy" : "Sell"}`}
        </Button>
        <span className="text-xs text-zinc-400">
          Contract: <code className="text-zinc-200">{contractId || "—"}</code>
        </span>
      </div>
    </Card>
  );
}
