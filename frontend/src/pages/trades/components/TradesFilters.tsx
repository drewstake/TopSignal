import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import { Toggle } from "../../../components/ui/Toggle";
import type { TradeOutcome, TradeSide } from "../../../mock/data";

export interface TradesFilterValues {
  query: string;
  side: "All" | TradeSide;
  outcome: "All" | Exclude<TradeOutcome, "Flat">;
  onlyBreaches: boolean;
}

export interface TradesFiltersProps {
  values: TradesFilterValues;
  onChange: (next: TradesFilterValues) => void;
}

export function TradesFilters({ values, onChange }: TradesFiltersProps) {
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      <Input
        value={values.query}
        onChange={(event) => onChange({ ...values, query: event.target.value })}
        placeholder="Filter symbol, strategy, notes"
        aria-label="Filter trades"
      />
      <Select
        value={values.side}
        onChange={(event) => onChange({ ...values, side: event.target.value as TradesFilterValues["side"] })}
        aria-label="Filter by side"
      >
        <option value="All">All sides</option>
        <option value="Long">Long</option>
        <option value="Short">Short</option>
      </Select>
      <Select
        value={values.outcome}
        onChange={(event) => onChange({ ...values, outcome: event.target.value as TradesFilterValues["outcome"] })}
        aria-label="Filter by outcome"
      >
        <option value="All">All outcomes</option>
        <option value="Win">Wins only</option>
        <option value="Loss">Losses only</option>
      </Select>
      <div className="flex items-center rounded-xl border border-slate-800 bg-slate-900/55 px-3">
        <Toggle
          checked={values.onlyBreaches}
          onChange={(checked) => onChange({ ...values, onlyBreaches: checked })}
          label="Rule breaches"
        />
      </div>
    </div>
  );
}
