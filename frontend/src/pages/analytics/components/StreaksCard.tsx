import { Badge } from "../../../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { streakStats } from "../../../mock/data";

const toneMap = {
  positive: "positive",
  negative: "negative",
  neutral: "accent",
} as const;

export function StreaksCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Streak Analysis</CardTitle>
        <CardDescription>Consistency and recovery snapshots.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2">
        {streakStats.map((stat) => (
          <div key={stat.label} className="rounded-xl border border-slate-800 bg-slate-900/55 p-4">
            <p className="text-xs uppercase tracking-wide text-slate-500">{stat.label}</p>
            <p className="mt-2 text-xl font-semibold text-slate-100">{stat.value}</p>
            <div className="mt-3 flex items-center justify-between">
              <Badge variant={toneMap[stat.tone]}>{stat.tone}</Badge>
              <span className="text-xs text-slate-400">{stat.helper}</span>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
