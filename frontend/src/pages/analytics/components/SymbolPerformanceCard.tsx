import { Badge } from "../../../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../../components/ui/Table";
import { symbolPerformance } from "../../../mock/data";

export function SymbolPerformanceCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Symbol Performance</CardTitle>
        <CardDescription>Top symbols by net performance and efficiency.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-xl border border-slate-800">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead>Trades</TableHead>
                <TableHead>Win Rate</TableHead>
                <TableHead>Avg Hold</TableHead>
                <TableHead>PnL</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {symbolPerformance.map((row) => (
                <TableRow key={row.symbol}>
                  <TableCell className="font-medium">{row.symbol}</TableCell>
                  <TableCell>{row.trades}</TableCell>
                  <TableCell>
                    <Badge variant={row.winRate >= 55 ? "positive" : "warning"}>{row.winRate}%</Badge>
                  </TableCell>
                  <TableCell>{row.avgHold}</TableCell>
                  <TableCell className={row.pnl >= 0 ? "text-emerald-300" : "text-rose-300"}>
                    {row.pnl >= 0 ? "+" : ""}${row.pnl.toLocaleString()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}
