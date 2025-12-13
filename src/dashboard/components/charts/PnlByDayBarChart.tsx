import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { DayPoint } from "../../../types/metrics";
import { fmtMoney } from "../../../lib/format";

export default function PnlByDayBarChart({ data }: { data: DayPoint[] }) {
  return (
    <div className="h-72 min-h-[18rem] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data}>
          <CartesianGrid strokeOpacity={0.15} />
          <XAxis dataKey="date" tick={{ fontSize: 12 }} />
          <YAxis tick={{ fontSize: 12 }} tickFormatter={(v) => fmtMoney(Number(v))} />
          <Tooltip formatter={(v) => fmtMoney(Number(v))} />
          <Bar dataKey="netPnl" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
