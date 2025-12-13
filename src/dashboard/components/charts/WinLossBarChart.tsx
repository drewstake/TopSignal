import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip } from "recharts";
import { fmtMoney } from "../../../lib/format";

type Row = { label: string; value: number };

export default function WinLossBarChart({ data }: { data: Row[] }) {
  return (
    <div className="h-[240px]">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="label" tick={{ fontSize: 12 }} />
          <YAxis tick={{ fontSize: 12 }} tickFormatter={(v) => `$${Math.round(Number(v))}`} />
          <Tooltip formatter={(v: unknown) => [fmtMoney(Number(v)), "Avg"]} />
          <Bar dataKey="value" fill="currentColor" radius={[6, 6, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
