import { Badge } from "../../../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { cn } from "../../../components/ui/cn";
import type { JournalEntry } from "../../../lib/types";

export interface JournalListProps {
  entries: JournalEntry[];
  selectedId: number | null;
  totalEntries: number;
  onSelect: (id: number) => void;
}

const moodVariant = {
  Focused: "positive",
  Neutral: "accent",
  Frustrated: "negative",
  Confident: "warning",
} as const;

const entryDateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

function formatEntryDate(value: string) {
  return entryDateFormatter.format(new Date(`${value}T00:00:00.000Z`));
}

function formatCurrency(value: number) {
  const normalized = Number.isFinite(value) ? value : 0;
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(normalized);
}

function buildPreview(body: string) {
  const normalized = body.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "No notes added yet.";
  }
  return normalized.length > 140 ? `${normalized.slice(0, 140).trimEnd()}...` : normalized;
}

export function JournalList({ entries, selectedId, totalEntries, onSelect }: JournalListProps) {
  return (
    <Card className="h-full">
      <CardHeader className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle>Journal Entries</CardTitle>
          <CardDescription>Review session notes, mood, tags, and saved trade snapshots.</CardDescription>
        </div>
        <div className="rounded-xl border border-slate-800/80 bg-slate-950/45 px-3 py-2 text-right">
          <p className="text-[11px] uppercase tracking-[0.16em] text-slate-500">Matches</p>
          <p className="text-lg font-semibold text-slate-100">{totalEntries}</p>
        </div>
      </CardHeader>
      <CardContent className="space-y-0">
        {entries.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-700/80 bg-slate-950/35 px-4 py-8 text-center">
            <p className="text-sm font-medium text-slate-200">No entries match these filters.</p>
            <p className="mt-2 text-sm text-slate-400">Adjust the search or date range, or create a new journal entry.</p>
          </div>
        ) : (
          <div className="space-y-3 xl:max-h-[calc(100vh-24rem)] xl:overflow-y-auto xl:pr-1">
            {entries.map((entry) => {
              const isActive = selectedId === entry.id;
              const preview = buildPreview(entry.body);
              const stats = entry.stats_json;
              const statsNet = stats?.net_realized_pnl ?? stats?.net ?? null;
              const visibleTags = entry.tags.slice(0, 3);
              const hiddenTagCount = Math.max(0, entry.tags.length - visibleTags.length);

              return (
                <button
                  type="button"
                  key={entry.id}
                  onClick={() => onSelect(entry.id)}
                  className={cn(
                    "group w-full rounded-2xl border px-4 py-3 text-left transition duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60",
                    isActive
                      ? "border-cyan-400/60 bg-cyan-500/10 shadow-[0_12px_30px_-24px_rgba(34,211,238,0.9)]"
                      : "border-slate-800/90 bg-slate-950/35 hover:border-slate-700 hover:bg-slate-900/70",
                  )}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 space-y-2">
                      <div className="flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-[0.14em] text-slate-500">
                        <span>{formatEntryDate(entry.entry_date)}</span>
                        {stats ? <span className="text-cyan-300/80">Snapshot saved</span> : null}
                      </div>
                      <p className="truncate text-sm font-semibold text-slate-100">{entry.title || "Untitled entry"}</p>
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      <Badge variant={moodVariant[entry.mood]}>{entry.mood}</Badge>
                      {entry.is_archived ? <Badge variant="neutral">Archived</Badge> : null}
                    </div>
                  </div>

                  <p className="mt-3 text-sm leading-6 text-slate-300">{preview}</p>

                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {visibleTags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full border border-slate-700/80 bg-slate-900/70 px-2.5 py-1 text-[11px] text-slate-300"
                      >
                        #{tag}
                      </span>
                    ))}
                    {hiddenTagCount > 0 ? (
                      <span className="rounded-full border border-slate-700/80 bg-slate-900/50 px-2.5 py-1 text-[11px] text-slate-400">
                        +{hiddenTagCount} more
                      </span>
                    ) : null}
                    {entry.tags.length === 0 ? (
                      <span className="text-[11px] text-slate-500">No tags</span>
                    ) : null}
                  </div>

                  <div className="mt-3 grid gap-2 rounded-xl border border-slate-800/80 bg-slate-950/45 px-3 py-2 text-xs text-slate-400 sm:grid-cols-3">
                    <div>
                      <p className="uppercase tracking-[0.14em] text-slate-500">Updated</p>
                      <p className="mt-1 text-sm text-slate-200">{new Date(entry.updated_at).toLocaleDateString("en-US")}</p>
                    </div>
                    <div>
                      <p className="uppercase tracking-[0.14em] text-slate-500">Trades</p>
                      <p className="mt-1 text-sm text-slate-200">{stats?.trade_count ?? 0}</p>
                    </div>
                    <div>
                      <p className="uppercase tracking-[0.14em] text-slate-500">Net PnL</p>
                      <p className={cn("mt-1 text-sm font-medium", statsNet === null ? "text-slate-300" : statsNet >= 0 ? "text-emerald-300" : "text-rose-300")}>
                        {statsNet === null ? "Not pulled" : `${statsNet > 0 ? "+" : ""}${formatCurrency(statsNet)}`}
                      </p>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
