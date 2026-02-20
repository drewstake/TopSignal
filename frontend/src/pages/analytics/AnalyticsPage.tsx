import { StreaksCard } from "./components/StreaksCard";
import { SymbolPerformanceCard } from "./components/SymbolPerformanceCard";
import { TimeOfDayHeatmapCard } from "./components/TimeOfDayHeatmapCard";

export function AnalyticsPage() {
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <div className="xl:col-span-2">
        <SymbolPerformanceCard />
      </div>
      <div className="xl:col-span-1">
        <StreaksCard />
      </div>
      <div className="xl:col-span-3">
        <TimeOfDayHeatmapCard />
      </div>
    </div>
  );
}
