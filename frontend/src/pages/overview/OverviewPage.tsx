import { Badge } from "../../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { kpiMetrics } from "../../mock/data";
import { EquityCurveCard } from "./components/EquityCurveCard";
import { PnlByDayCard } from "./components/PnlByDayCard";
import { RecentTradesCard } from "./components/RecentTradesCard";
import { RiskRulesCard } from "./components/RiskRulesCard";

export function OverviewPage() {
  return (
    <div className="space-y-4">
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        {kpiMetrics.map((metric) => (
          <Card key={metric.id} className="p-4">
            <CardHeader className="mb-3">
              <CardDescription>{metric.label}</CardDescription>
              <CardTitle className="text-xl">{metric.value}</CardTitle>
            </CardHeader>
            <CardContent className="flex items-center justify-between text-xs">
              <Badge variant={metric.changePct >= 0 ? "positive" : "negative"}>
                {metric.changePct >= 0 ? "+" : ""}
                {metric.changePct.toFixed(1)}%
              </Badge>
              <span className="text-slate-400">{metric.hint}</span>
            </CardContent>
          </Card>
        ))}
      </section>

      <section className="grid gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <EquityCurveCard />
        </div>
        <div className="xl:col-span-1">
          <RiskRulesCard />
        </div>
        <div className="xl:col-span-2">
          <PnlByDayCard />
        </div>
        <div className="xl:col-span-3">
          <RecentTradesCard />
        </div>
      </section>
    </div>
  );
}
