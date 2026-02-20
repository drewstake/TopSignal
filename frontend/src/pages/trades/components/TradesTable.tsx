import { Badge } from "../../../components/ui/Badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../../components/ui/Table";
import type { Trade } from "../../../mock/data";

export interface TradesTableProps {
  trades: Trade[];
  onSelect: (trade: Trade) => void;
}

export function TradesTable({ trades, onSelect }: TradesTableProps) {
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/40">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>ID</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Side</TableHead>
            <TableHead>Strategy</TableHead>
            <TableHead>R Multiple</TableHead>
            <TableHead>PnL</TableHead>
            <TableHead>Closed</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {trades.map((trade) => (
            <TableRow
              key={trade.id}
              className="cursor-pointer focus-within:bg-slate-800/70"
              onClick={() => onSelect(trade)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  onSelect(trade);
                }
              }}
              tabIndex={0}
            >
              <TableCell className="text-slate-400">{trade.id}</TableCell>
              <TableCell className="font-medium">{trade.symbol}</TableCell>
              <TableCell>
                <Badge variant={trade.side === "Long" ? "accent" : "warning"}>{trade.side}</Badge>
              </TableCell>
              <TableCell>{trade.strategy}</TableCell>
              <TableCell className={trade.riskMultiple >= 0 ? "text-emerald-300" : "text-rose-300"}>
                {trade.riskMultiple >= 0 ? "+" : ""}
                {trade.riskMultiple.toFixed(1)}R
              </TableCell>
              <TableCell className={trade.pnl >= 0 ? "text-emerald-300" : "text-rose-300"}>
                {trade.pnl >= 0 ? "+" : ""}${trade.pnl.toFixed(2)}
              </TableCell>
              <TableCell className="text-slate-400">{trade.closedAt}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
