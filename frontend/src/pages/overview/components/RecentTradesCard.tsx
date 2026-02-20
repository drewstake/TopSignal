import { Badge } from "../../../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../../components/ui/Table";
import { mockTrades } from "../../../mock/data";

const recentTrades = mockTrades.slice(0, 6);

export function RecentTradesCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent Trades</CardTitle>
        <CardDescription>Most recent fills from local mock data.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-xl border border-slate-800">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead>Side</TableHead>
                <TableHead>Strategy</TableHead>
                <TableHead>PnL</TableHead>
                <TableHead>Closed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recentTrades.map((trade) => (
                <TableRow key={trade.id}>
                  <TableCell className="font-medium">{trade.symbol}</TableCell>
                  <TableCell>
                    <Badge variant={trade.side === "Long" ? "accent" : "warning"}>{trade.side}</Badge>
                  </TableCell>
                  <TableCell>{trade.strategy}</TableCell>
                  <TableCell className={trade.pnl >= 0 ? "text-emerald-300" : "text-rose-300"}>
                    {trade.pnl >= 0 ? "+" : ""}${trade.pnl.toFixed(2)}
                  </TableCell>
                  <TableCell className="text-slate-400">{trade.closedAt}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}
