import { Badge } from "../../../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import type { JournalEntry } from "../../../lib/types";

export interface JournalListProps {
  entries: JournalEntry[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}

const moodVariant = {
  Focused: "positive",
  Neutral: "accent",
  Frustrated: "negative",
  Confident: "warning",
} as const;

export function JournalList({ entries, selectedId, onSelect }: JournalListProps) {
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>Journal Entries</CardTitle>
        <CardDescription>Session notes and execution reflections by account.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {entries.length === 0 ? (
          <p className="rounded-xl border border-slate-800 bg-slate-900/50 px-3 py-6 text-center text-sm text-slate-400">
            No journal entries match the current filters.
          </p>
        ) : null}
        {entries.map((entry) => {
          const isActive = selectedId === entry.id;

          return (
            <button
              type="button"
              key={entry.id}
              onClick={() => onSelect(entry.id)}
              className={`w-full rounded-xl border px-3 py-3 text-left transition duration-200 ${
                isActive
                  ? "border-cyan-400/60 bg-cyan-500/10"
                  : "border-slate-800 bg-slate-900/55 hover:border-slate-700 hover:bg-slate-900/75"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-medium text-slate-100">{entry.title}</p>
                <div className="flex items-center gap-2">
                  <Badge variant={moodVariant[entry.mood]}>{entry.mood}</Badge>
                  {entry.is_archived ? <Badge variant="neutral">Archived</Badge> : null}
                </div>
              </div>
              <p className="mt-1 text-xs text-slate-400">{entry.entry_date}</p>
            </button>
          );
        })}
      </CardContent>
    </Card>
  );
}
