import { Badge } from "../../../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import type { JournalEntry } from "../../../mock/data";

export interface JournalListProps {
  entries: JournalEntry[];
  selectedId: string;
  onSelect: (id: string) => void;
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
        <CardDescription>Session notes and execution reflections.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
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
                <Badge variant={moodVariant[entry.mood]}>{entry.mood}</Badge>
              </div>
              <p className="mt-1 text-xs text-slate-400">{entry.date}</p>
            </button>
          );
        })}
      </CardContent>
    </Card>
  );
}
