import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtMoney } from "../../../lib/format";

type Props = { data: { label: string; trades: number; netPnl: number }[] };

export default function TradeTimeHistogram({ data }: Props) {
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
          <Tooltip formatter={(v, name) => (name === "Net PnL" ? fmtMoney(Number(v)) : `${v}`)} />
          <Legend />
          <Bar yAxisId="left" dataKey="trades" name="Trades" fill="#a5b4fc" radius={[4, 4, 0, 0]} />
          <Line yAxisId="right" type="monotone" dataKey="netPnl" name="Net PnL" stroke="#34d399" dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
