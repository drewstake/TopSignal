import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtMoney } from "../../../lib/format";

type Props = { data: { label: string; trades: number; netPnl: number }[] };

export default function TradeTimeHistogram({ data }: Props) {
  const renderTooltip = ({ active, payload, label }: any) => {
    if (!active || !payload?.length) return null;

    const trades = payload.find((p: any) => p.dataKey === "trades")?.value ?? 0;
    const netPnl = payload.find((p: any) => p.dataKey === "netPnl")?.value ?? 0;

    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-950/90 px-3 py-2 text-xs text-zinc-100 shadow-lg">
        <div className="mb-1 text-[11px] uppercase tracking-wide text-zinc-400">{label} ET</div>
        <div className="flex items-center justify-between gap-6">
          <div>Trades</div>
          <div className="font-semibold text-indigo-200">{trades}</div>
        </div>
        <div className="flex items-center justify-between gap-6">
          <div>Net PnL</div>
          <div className="font-semibold text-emerald-200">{fmtMoney(Number(netPnl))}</div>
        </div>
      </div>
    );
  };

  return (
    <div className="h-72 min-h-[18rem] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ left: 8 }}>
          <CartesianGrid strokeOpacity={0.15} />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} interval={0} angle={-45} textAnchor="end" height={60} />
          <YAxis yAxisId="left" tick={{ fontSize: 12 }} tickFormatter={(v) => `${v}`.padStart(1, "0")} width={36} />
          <YAxis
            yAxisId="right"
            orientation="right"
            tick={{ fontSize: 12 }}
            tickFormatter={(v) => fmtMoney(Number(v))}
            width={60}
          />
          <Tooltip content={renderTooltip} />
          <Legend />
          <Bar yAxisId="left" dataKey="trades" name="Trades" fill="#a5b4fc" radius={[4, 4, 0, 0]} />
          <Line yAxisId="right" type="monotone" dataKey="netPnl" name="Net PnL" stroke="#34d399" dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
