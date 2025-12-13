import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtMoney } from "../../../lib/format";

type Props = { data: { label: string; netPnl: number; trades: number }[] };

export default function DayOfWeekBarChart({ data }: Props) {
  return (
    <div className="h-64 min-h-[16rem] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ left: 8 }}>
          <CartesianGrid strokeOpacity={0.15} />
          <XAxis dataKey="label" tick={{ fontSize: 12 }} />
          <YAxis tick={{ fontSize: 12 }} tickFormatter={(v) => fmtMoney(Number(v))} width={64} />
          <Tooltip formatter={(v, name) => (name === "Net PnL" ? fmtMoney(Number(v)) : `${v}`)} />
          <Bar dataKey="netPnl" name="Net PnL" fill="#34d399" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
