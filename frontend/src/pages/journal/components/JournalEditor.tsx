import { type ChangeEvent, type ClipboardEvent, useRef } from "react";

import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import type { JournalEntry, JournalEntryImage, JournalMood } from "../../../lib/types";
import { handleClipboardImagePaste } from "../journalClipboard";
import type { JournalSaveState } from "../journalAutosave";
import type { JournalDraft } from "../journalUtils";

export interface JournalEditorProps {
  entry: JournalEntry | null;
  draft: JournalDraft | null;
  saveState: JournalSaveState;
  savingDisabled: boolean;
  conflictServerEntry: JournalEntry | null;
  images: JournalEntryImage[];
  imagesLoading: boolean;
  imagesError: string | null;
  uploadingImage: boolean;
  pullingStats: boolean;
  pullStatsError: string | null;
  deletingEntry: boolean;
  onDraftChange: (next: JournalDraft) => void;
  onArchiveToggle: () => void;
  onRetrySave: () => void;
  onReloadServerVersion: () => void;
  onUploadImage: (file: File | Blob) => void;
  onDeleteImage: (imageId: number) => void;
  onPullTradeStats: () => void;
  onDeleteEntry: () => void;
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

function formatNumber(value: number) {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function JournalEditor({
  entry,
  draft,
  saveState,
  savingDisabled,
  conflictServerEntry,
  images,
  imagesLoading,
  imagesError,
  uploadingImage,
  pullingStats,
  pullStatsError,
  deletingEntry,
  onDraftChange,
  onArchiveToggle,
  onRetrySave,
  onReloadServerVersion,
  onUploadImage,
  onDeleteImage,
  onPullTradeStats,
  onDeleteEntry,
}: JournalEditorProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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

  const handlePaste = (event: ClipboardEvent<HTMLDivElement>) => {
    handleClipboardImagePaste(event, onUploadImage);
  };

  const handleFileInput = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    onUploadImage(file);
    event.target.value = "";
  };

  const stats = entry.stats_json;

  return (
    <Card className="h-full">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>{entry.entry_date}</CardTitle>
            <CardDescription>
              {saveStateLabel[saveState]} {"\u2022"} v{draft.version}
            </CardDescription>
          </div>
          <Badge variant={moodVariant[draft.mood]}>{draft.mood}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4" onPaste={handlePaste}>
        {conflictServerEntry ? (
          <div className="rounded-xl border border-amber-500/45 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
            <p className="font-medium">This entry changed elsewhere.</p>
            <div className="mt-2 flex justify-end">
              <Button size="sm" variant="secondary" onClick={onReloadServerVersion}>
                Reload server version
              </Button>
            </div>
          </div>
        ) : null}

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
            <Select value={draft.mood} onChange={(event) => onDraftChange({ ...draft, mood: event.target.value as JournalMood })}>
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

        <div className="rounded-xl border border-slate-800 bg-slate-900/45 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-500">Images</p>
              <p className="text-xs text-slate-400">Paste image with Ctrl+V while focused in the editor.</p>
            </div>
            <div className="flex items-center gap-2">
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="hidden"
                onChange={handleFileInput}
              />
              <Button size="sm" variant="secondary" onClick={() => fileInputRef.current?.click()} disabled={uploadingImage}>
                {uploadingImage ? "Uploading..." : "Choose Image"}
              </Button>
            </div>
          </div>

          {imagesError ? <p className="mt-2 text-sm text-rose-300">{imagesError}</p> : null}
          {imagesLoading ? <p className="mt-2 text-sm text-slate-400">Loading images...</p> : null}

          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {images.map((image) => (
              <div key={image.id} className="overflow-hidden rounded-lg border border-slate-700/80 bg-slate-950/60">
                <img src={image.url} alt="Journal upload" className="h-28 w-full object-cover" loading="lazy" />
                <div className="flex items-center justify-between px-2 py-2">
                  <p className="text-[11px] text-slate-400">{Math.round(image.byte_size / 1024)} KB</p>
                  <Button size="sm" variant="ghost" onClick={() => onDeleteImage(image.id)}>
                    Delete
                  </Button>
                </div>
              </div>
            ))}
            {images.length === 0 && !imagesLoading ? <p className="text-xs text-slate-500">No images uploaded.</p> : null}
          </div>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/45 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs uppercase tracking-wide text-slate-500">Trade Stats Snapshot</p>
            <Button size="sm" variant="secondary" onClick={onPullTradeStats} disabled={pullingStats || savingDisabled}>
              {pullingStats ? "Pulling..." : "Pull trade stats"}
            </Button>
          </div>

          {pullStatsError ? <p className="mt-2 text-sm text-rose-300">{pullStatsError}</p> : null}

          {stats ? (
            <div className="mt-3 overflow-hidden rounded-lg border border-slate-800/80">
              <table className="w-full text-xs">
                <tbody>
                  <tr className="border-b border-slate-800/70">
                    <td className="px-2 py-1.5 text-slate-400">Trade Count</td>
                    <td className="px-2 py-1.5 text-right text-slate-200">{stats.trade_count}</td>
                  </tr>
                  <tr className="border-b border-slate-800/70">
                    <td className="px-2 py-1.5 text-slate-400">Win Rate</td>
                    <td className="px-2 py-1.5 text-right text-slate-200">{formatNumber(stats.win_rate)}%</td>
                  </tr>
                  <tr className="border-b border-slate-800/70">
                    <td className="px-2 py-1.5 text-slate-400">Gross / Net</td>
                    <td className="px-2 py-1.5 text-right text-slate-200">
                      {formatNumber(stats.gross)} / {formatNumber(stats.net)}
                    </td>
                  </tr>
                  <tr className="border-b border-slate-800/70">
                    <td className="px-2 py-1.5 text-slate-400">Fees</td>
                    <td className="px-2 py-1.5 text-right text-slate-200">{formatNumber(stats.total_fees)}</td>
                  </tr>
                  <tr className="border-b border-slate-800/70">
                    <td className="px-2 py-1.5 text-slate-400">Avg Win / Avg Loss</td>
                    <td className="px-2 py-1.5 text-right text-slate-200">
                      {formatNumber(stats.avg_win)} / {formatNumber(stats.avg_loss)}
                    </td>
                  </tr>
                  <tr>
                    <td className="px-2 py-1.5 text-slate-400">Largest Win / Loss</td>
                    <td className="px-2 py-1.5 text-right text-slate-200">
                      {formatNumber(stats.largest_win)} / {formatNumber(stats.largest_loss)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          ) : (
            <p className="mt-2 text-xs text-slate-500">No stats snapshot pulled yet.</p>
          )}
        </div>

        <div className="flex flex-wrap justify-end gap-2">
          {saveState === "error" ? (
            <Button variant="secondary" onClick={onRetrySave} disabled={savingDisabled}>
              Retry Save
            </Button>
          ) : null}
          <Button variant="ghost" onClick={onArchiveToggle} disabled={savingDisabled}>
            {draft.is_archived ? "Unarchive" : "Archive"}
          </Button>
          <Button variant="danger" onClick={onDeleteEntry} disabled={deletingEntry || savingDisabled}>
            {deletingEntry ? "Deleting..." : "Delete Entry"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
