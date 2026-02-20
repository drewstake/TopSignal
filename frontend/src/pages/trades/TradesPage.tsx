import { useMemo, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { mockTrades, type Trade } from "../../mock/data";
import { TradeDrawer } from "./components/TradeDrawer";
import { TradesFilters, type TradesFilterValues } from "./components/TradesFilters";
import { TradesTable } from "./components/TradesTable";

const initialFilters: TradesFilterValues = {
  query: "",
  side: "All",
  outcome: "All",
  onlyBreaches: false,
};

export function TradesPage() {
  const [filters, setFilters] = useState<TradesFilterValues>(initialFilters);
  const [activeTrade, setActiveTrade] = useState<Trade | null>(null);

  const filteredTrades = useMemo(() => {
    return mockTrades.filter((trade) => {
      const byQuery =
        filters.query.trim().length === 0 ||
        [trade.symbol, trade.strategy, trade.notes].join(" ").toLowerCase().includes(filters.query.toLowerCase());

      const bySide = filters.side === "All" || trade.side === filters.side;
      const byOutcome =
        filters.outcome === "All" ||
        (filters.outcome === "Win" && trade.pnl > 0) ||
        (filters.outcome === "Loss" && trade.pnl < 0);
      const byRule = !filters.onlyBreaches || trade.ruleBreached;

      return byQuery && bySide && byOutcome && byRule;
    });
  }, [filters]);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Trades</CardTitle>
          <CardDescription>Filter and inspect trades. Click any row to open details.</CardDescription>
        </CardHeader>
        <CardContent>
          <TradesFilters values={filters} onChange={setFilters} />
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <TradesTable trades={filteredTrades} onSelect={setActiveTrade} />
          {filteredTrades.length === 0 ? (
            <p className="mt-3 text-sm text-slate-400">No trades match the current filters.</p>
          ) : null}
        </CardContent>
      </Card>

      <TradeDrawer trade={activeTrade} open={activeTrade !== null} onClose={() => setActiveTrade(null)} />
    </div>
  );
}
