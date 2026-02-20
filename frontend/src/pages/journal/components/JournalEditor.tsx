import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import type { JournalEntry } from "../../../mock/data";

export interface JournalEditorProps {
  entry: JournalEntry;
}

const moodVariant = {
  Focused: "positive",
  Neutral: "accent",
  Frustrated: "negative",
  Confident: "warning",
} as const;

export function JournalEditor({ entry }: JournalEditorProps) {
  return (
    <Card className="h-full">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>{entry.title}</CardTitle>
            <CardDescription>{entry.date}</CardDescription>
          </div>
          <Badge variant={moodVariant[entry.mood]}>{entry.mood}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-xl border border-slate-800 bg-slate-900/55 p-3">
          <p className="text-xs uppercase tracking-wide text-slate-500">Tags</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {entry.tags.map((tag) => (
              <Badge key={tag} variant="neutral">
                {tag}
              </Badge>
            ))}
          </div>
        </div>
        <label className="block space-y-2">
          <span className="text-xs uppercase tracking-wide text-slate-500">Entry</span>
          <textarea
            readOnly
            value={entry.body}
            className="min-h-48 w-full rounded-xl border border-slate-700 bg-slate-950/70 p-3 text-sm text-slate-200 outline-none"
          />
        </label>
        <div className="flex justify-end gap-2">
          <Button variant="ghost">Archive</Button>
          <Button variant="secondary">Save Draft</Button>
        </div>
      </CardContent>
    </Card>
  );
}
