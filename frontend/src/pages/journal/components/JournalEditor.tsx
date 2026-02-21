import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import type { JournalEntry, JournalMood } from "../../../lib/types";
import type { JournalSaveState } from "../journalAutosave";
import type { JournalDraft } from "../journalUtils";

export interface JournalEditorProps {
  entry: JournalEntry | null;
  draft: JournalDraft | null;
  saveState: JournalSaveState;
  savingDisabled: boolean;
  onDraftChange: (next: JournalDraft) => void;
  onArchiveToggle: () => void;
  onRetrySave: () => void;
}

const moodVariant = {
  Focused: "positive",
  Neutral: "accent",
  Frustrated: "negative",
  Confident: "warning",
} as const;

const moodOptions: JournalMood[] = ["Focused", "Neutral", "Frustrated", "Confident"];

const saveStateLabel: Record<JournalSaveState, string> = {
  saved: "Saved",
  saving: "Saving...",
  unsaved: "Unsaved changes",
  error: "Save failed",
};

export function JournalEditor({
  entry,
  draft,
  saveState,
  savingDisabled,
  onDraftChange,
  onArchiveToggle,
  onRetrySave,
}: JournalEditorProps) {
  if (!entry || !draft) {
    return (
      <Card className="h-full">
        <CardHeader>
          <CardTitle>Journal Entry</CardTitle>
          <CardDescription>Select an entry or create a new one.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card className="h-full">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>{entry.entry_date}</CardTitle>
            <CardDescription>{saveStateLabel[saveState]}</CardDescription>
          </div>
          <Badge variant={moodVariant[draft.mood]}>{draft.mood}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 md:grid-cols-2">
          <label className="block space-y-1">
            <span className="text-xs uppercase tracking-wide text-slate-500">Title</span>
            <Input
              value={draft.title}
              maxLength={160}
              onChange={(event) => onDraftChange({ ...draft, title: event.target.value })}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-xs uppercase tracking-wide text-slate-500">Mood</span>
            <Select
              value={draft.mood}
              onChange={(event) => onDraftChange({ ...draft, mood: event.target.value as JournalMood })}
            >
              {moodOptions.map((mood) => (
                <option key={mood} value={mood}>
                  {mood}
                </option>
              ))}
            </Select>
          </label>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/55 p-3">
          <p className="text-xs uppercase tracking-wide text-slate-500">Tags</p>
          <Input
            className="mt-2"
            value={draft.tagsInput}
            maxLength={1024}
            onChange={(event) => onDraftChange({ ...draft, tagsInput: event.target.value })}
            placeholder="comma,separated,tags"
          />
        </div>
        <label className="block space-y-2">
          <span className="text-xs uppercase tracking-wide text-slate-500">Entry</span>
          <textarea
            value={draft.body}
            maxLength={20000}
            onChange={(event) => onDraftChange({ ...draft, body: event.target.value })}
            className="min-h-48 w-full rounded-xl border border-slate-700 bg-slate-950/70 p-3 text-sm text-slate-200 outline-none"
          />
        </label>
        <div className="flex flex-wrap justify-end gap-2">
          {saveState === "error" ? (
            <Button variant="secondary" onClick={onRetrySave} disabled={savingDisabled}>
              Retry Save
            </Button>
          ) : null}
          <Button variant="ghost" onClick={onArchiveToggle} disabled={savingDisabled}>
            {draft.is_archived ? "Unarchive" : "Archive"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
