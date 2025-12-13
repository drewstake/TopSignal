import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { EquityPoint } from "../../../types/metrics";
import { fmtMoney } from "../../../lib/format";

export default function EquityCurveChart({ data }: { data: EquityPoint[] }) {
  return (
    <div className="h-72 min-h-[18rem] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <CartesianGrid strokeOpacity={0.15} />
          <XAxis dataKey="date" tick={{ fontSize: 12 }} />
          <YAxis tick={{ fontSize: 12 }} tickFormatter={(v) => fmtMoney(Number(v))} />
          <Tooltip
            formatter={(value, name) => {
              if (name === "equity") return [fmtMoney(Number(value)), "Equity"];
              if (name === "pnl") return [fmtMoney(Number(value)), "Day PnL"];
              return [String(value), String(name)];
            }}
          />
          <Line type="monotone" dataKey="equity" dot={false} strokeWidth={2} />
          <Line type="monotone" dataKey="pnl" dot={false} strokeWidth={1} strokeOpacity={0.6} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
