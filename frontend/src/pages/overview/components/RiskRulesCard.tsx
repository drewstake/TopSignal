import { Badge } from "../../../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { Progress } from "../../../components/ui/Progress";
import { riskRules } from "../../../mock/data";

const badgeByStatus = {
  good: "positive",
  warning: "warning",
  risk: "negative",
} as const;

export function RiskRulesCard() {
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>Risk & Rules</CardTitle>
        <CardDescription>Policy adherence and guardrail utilization.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {riskRules.map((rule) => (
          <div key={rule.id} className="space-y-2 rounded-xl border border-slate-800 bg-slate-900/60 p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-medium text-slate-200">{rule.name}</p>
              <Badge variant={badgeByStatus[rule.status]}>{rule.status}</Badge>
            </div>
            <Progress value={rule.progress} />
            <p className="text-xs text-slate-400">{rule.detail}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
