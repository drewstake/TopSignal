import { memo } from "react";

import { Badge } from "../../../components/ui/Badge";
import { Card, CardContent, CardHeader, CardTitle } from "../../../components/ui/Card";
import { cn } from "../../../components/ui/cn";
import type { JournalEntry } from "../../../lib/types";
import { stripJournalImageMarkdown } from "../journalImages";

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
  const normalized = stripJournalImageMarkdown(body).replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "No notes added yet.";
  }
  return normalized;
}

function JournalListInner({ entries, selectedId, totalEntries, onSelect }: JournalListProps) {
  return (
    <Card className="h-full xl:flex xl:min-h-0 xl:flex-col">
      <CardHeader className="mb-3 flex items-center justify-between gap-3 space-y-0">
        <div className="min-w-0">
          <CardTitle className="shrink-0">Journal Entries</CardTitle>
        </div>
        <div className="shrink-0 rounded-full border border-slate-800/80 bg-slate-950/45 px-2.5 py-1 text-[11px] text-slate-400">
          Matches <span className="ml-1 font-semibold text-slate-100">{totalEntries}</span>
        </div>
      </CardHeader>
      <CardContent className="space-y-0 xl:flex-1 xl:min-h-0 xl:overflow-hidden">
        {entries.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-700/80 bg-slate-950/35 px-4 py-8 text-center xl:flex xl:h-full xl:flex-col xl:justify-center">
            <p className="text-sm font-medium text-slate-200">No entries match these filters.</p>
            <p className="mt-2 text-sm text-slate-400">Adjust the search or date range, or create a new journal entry.</p>
          </div>
        ) : (
          <div className="space-y-2.5 xl:h-full xl:overflow-y-auto xl:pr-1">
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
                    "group flex h-[220px] w-full flex-col rounded-xl border px-3 py-2.5 text-left transition duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60",
                    isActive
                      ? "border-cyan-400/60 bg-cyan-500/10 shadow-[0_12px_30px_-24px_rgba(34,211,238,0.9)]"
                      : "border-slate-800/90 bg-slate-950/35 hover:border-slate-700 hover:bg-slate-900/70",
                  )}
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0 space-y-1.5">
                      <div className="flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-[0.14em] text-slate-500">
                        <span>{formatEntryDate(entry.entry_date)}</span>
                      </div>
                      <p className="truncate text-[13px] font-semibold text-slate-100">{entry.title || "Untitled entry"}</p>
                      <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
                        <span className="rounded-full border border-slate-700/80 bg-slate-950/45 px-2 py-0.5 text-slate-400">
                          {stats?.trade_count ?? 0} trades
                        </span>
                        <span
                          className={cn(
                            "rounded-full border px-2 py-0.5 font-medium",
                            statsNet === null
                              ? "border-slate-700/80 bg-slate-950/45 text-slate-300"
                              : statsNet >= 0
                                ? "border-emerald-400/30 bg-emerald-500/10 text-emerald-300"
                                : "border-rose-400/30 bg-rose-500/10 text-rose-300",
                          )}
                        >
                          {statsNet === null ? "Not pulled" : `${statsNet > 0 ? "+" : ""}${formatCurrency(statsNet)}`}
                        </span>
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      <Badge variant={moodVariant[entry.mood]}>{entry.mood}</Badge>
                      {entry.is_archived ? <Badge variant="neutral">Archived</Badge> : null}
                    </div>
                  </div>

                  <div className="mt-2 flex-1 overflow-hidden">
                    <p className="text-xs leading-5 text-slate-300">{preview}</p>
                  </div>

                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {visibleTags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full border border-slate-700/80 bg-slate-900/70 px-2 py-0.5 text-[10px] text-slate-300"
                      >
                        #{tag}
                      </span>
                    ))}
                    {hiddenTagCount > 0 ? (
                      <span className="rounded-full border border-slate-700/80 bg-slate-900/50 px-2 py-0.5 text-[10px] text-slate-400">
                        +{hiddenTagCount} more
                      </span>
                    ) : null}
                    {entry.tags.length === 0 ? (
                      <span className="text-[10px] text-slate-500">No tags</span>
                    ) : null}
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

export const JournalList = memo(JournalListInner);
