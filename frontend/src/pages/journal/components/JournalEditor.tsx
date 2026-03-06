import { type ClipboardEvent } from "react";

import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import { Textarea } from "../../../components/ui/Textarea";
import type { JournalEntry, JournalEntryImage, JournalMood } from "../../../lib/types";
import { getClipboardImageFile } from "../journalClipboard";
import { extractPersistedJournalImageIds } from "../journalImages";
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
  deletingEntry: boolean;
  onDraftChange: (next: JournalDraft) => void;
  onArchiveToggle: () => void;
  onRetrySave: () => void;
  onReloadServerVersion: () => void;
  onPasteImage: (file: File, selection: { start: number; end: number }) => void;
  onDeleteImage: (imageId: number) => void;
  onDeleteEntry: () => void;
}

const moodVariant = {
  Focused: "positive",
  Neutral: "accent",
  Frustrated: "negative",
  Confident: "warning",
} as const;

const moodOptions: JournalMood[] = ["Focused", "Neutral", "Frustrated", "Confident"];

const saveStateMeta = {
  saved: {
    label: "Saved",
    variant: "positive",
    description: "All journal edits are synced for this entry.",
  },
  saving: {
    label: "Saving",
    variant: "accent",
    description: "Latest changes are being autosaved.",
  },
  unsaved: {
    label: "Unsaved",
    variant: "warning",
    description: "You have local edits waiting to sync.",
  },
  error: {
    label: "Save failed",
    variant: "negative",
    description: "Autosave hit an error. Retry to sync this draft.",
  },
} as const;

const entryDateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "long",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

const timestampFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZone: "America/New_York",
});

function formatEntryDate(value: string) {
  return entryDateFormatter.format(new Date(`${value}T00:00:00.000Z`));
}

function formatTimestamp(value: string | null) {
  if (!value) {
    return "Not available";
  }
  return timestampFormatter.format(new Date(value));
}

function formatFileSize(value: number) {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${Math.round(value / 1024)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function JournalEditor(props: JournalEditorProps) {
  const {
    entry,
    draft,
    saveState,
    conflictServerEntry,
    images,
    imagesLoading,
    imagesError,
    uploadingImage,
    onDraftChange,
    onReloadServerVersion,
    onPasteImage,
    onDeleteImage,
  } = props;

  if (!entry || !draft) {
    return (
      <Card className="h-full">
        <CardHeader>
          <CardTitle>Journal Workspace</CardTitle>
          <CardDescription>Select an entry from the list or create one to start writing.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="rounded-2xl border border-dashed border-slate-700/80 bg-slate-950/35 px-4 py-10 text-center">
            <p className="text-sm font-medium text-slate-200">No entry selected.</p>
            <p className="mt-2 text-sm text-slate-400">
              Pick a journal entry to edit notes, review trade stats, and revisit pasted charts.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  const handleNotesPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const imageFile = getClipboardImageFile(event.clipboardData?.items);
    if (!imageFile) {
      return;
    }

    event.preventDefault();
    onPasteImage(imageFile, {
      start: event.currentTarget.selectionStart ?? draft.body.length,
      end: event.currentTarget.selectionEnd ?? draft.body.length,
    });
  };

  const saveStateDisplay = saveStateMeta[saveState];
  const notesCharacterCount = draft.body.length;
  const imageSummary = `${images.length} ${images.length === 1 ? "image" : "images"}`;
  const embeddedImageIds = extractPersistedJournalImageIds(draft.body);
  const embeddedImageIdSet = new Set(embeddedImageIds);
  const embeddedImages = embeddedImageIds
    .map((imageId) => images.find((image) => image.id === imageId) ?? null)
    .filter((image): image is JournalEntryImage => image !== null);
  const unreferencedImages = images.filter((image) => !embeddedImageIdSet.has(image.id));
  const visibleImages = [...embeddedImages, ...unreferencedImages];
  const showImagePanel = imagesLoading || imagesError || uploadingImage || visibleImages.length > 0;

  return (
    <Card className="flex h-full min-h-0 flex-col overflow-hidden">
      <CardHeader className="space-y-2.5 border-b border-slate-800/70 pb-2.5 pt-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-sm font-semibold tracking-[0.02em] text-slate-200">
            {formatEntryDate(entry.entry_date)}
          </CardTitle>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant={saveStateDisplay.variant}>{saveStateDisplay.label}</Badge>
            <Badge variant={moodVariant[draft.mood]}>{draft.mood}</Badge>
            {draft.is_archived ? <Badge variant="neutral">Archived</Badge> : <Badge variant="accent">Active</Badge>}
          </div>
        </div>

        <div className="grid gap-2 rounded-xl border border-slate-800/75 bg-slate-950/35 p-2.5 md:grid-cols-[minmax(0,1.35fr)_150px_minmax(0,1fr)] md:items-end">
          <label className="block space-y-1">
            <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">Title</span>
            <Input
              value={draft.title}
              maxLength={160}
              placeholder="Summarize the session in one line"
              className="h-9 text-sm"
              onChange={(event) => onDraftChange({ ...draft, title: event.target.value })}
            />
          </label>

          <label className="block space-y-1">
            <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">Mood</span>
            <Select
              value={draft.mood}
              className="h-9 text-sm"
              onChange={(event) => onDraftChange({ ...draft, mood: event.target.value as JournalMood })}
            >
              {moodOptions.map((mood) => (
                <option key={mood} value={mood}>
                  {mood}
                </option>
              ))}
            </Select>
          </label>

          <label className="block space-y-1">
            <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">Tags</span>
            <Input
              value={draft.tagsInput}
              maxLength={1024}
              placeholder="nq, open-drive, patience, execution"
              className="h-9 text-sm"
              onChange={(event) => onDraftChange({ ...draft, tagsInput: event.target.value })}
            />
          </label>
        </div>
      </CardHeader>

      <CardContent className="flex min-h-0 flex-1 flex-col space-y-3 pt-3">
        {conflictServerEntry ? (
          <div className="rounded-2xl border border-amber-500/45 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
            <p className="font-medium">This entry changed somewhere else before your latest autosave completed.</p>
            <div className="mt-3 flex justify-end">
              <Button size="sm" variant="secondary" onClick={onReloadServerVersion}>
                Reload server version
              </Button>
            </div>
          </div>
        ) : null}

        <section className="flex min-h-0 flex-1 flex-col space-y-2.5 overflow-hidden rounded-[20px] border border-slate-800/80 bg-gradient-to-b from-slate-950/80 via-slate-950/45 to-slate-950/30 px-3 py-3 shadow-panel">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">Journal Entry</span>
            <span className="text-[11px] text-slate-500">{notesCharacterCount}/20,000</span>
          </div>
          <label className="block min-h-0 flex-1">
            <Textarea
              value={draft.body}
              maxLength={20000}
              className="h-full min-h-[360px] resize-none overflow-y-auto border-slate-800 bg-slate-950/70 leading-6"
              placeholder="What did the market do, how did you respond, what should you repeat or correct next time?"
              onPaste={handleNotesPaste}
              onChange={(event) => onDraftChange({ ...draft, body: event.target.value })}
            />
          </label>

          {uploadingImage ? (
            <div className="rounded-xl border border-slate-800/70 bg-slate-950/45 px-3 py-2 text-[11px] text-slate-400">
              Uploading pasted image...
            </div>
          ) : null}

          {imagesError ? (
            <p className="rounded-xl border border-rose-500/35 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
              {imagesError}
            </p>
          ) : null}

          {showImagePanel ? (
            <div className="space-y-3 rounded-[20px] border border-slate-800/75 bg-slate-950/35 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">Entry Images</p>
                {visibleImages.length > 0 ? (
                  <span className="rounded-full border border-slate-700/80 bg-slate-900/60 px-3 py-1 text-[11px] uppercase tracking-[0.14em] text-slate-300">
                    {imageSummary}
                  </span>
                ) : null}
              </div>

              {imagesLoading && visibleImages.length === 0 ? (
                <div className="rounded-2xl border border-slate-800/70 bg-slate-950/45 px-4 py-6 text-center text-sm text-slate-400">
                  Loading images...
                </div>
              ) : null}

              {visibleImages.length > 0 ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  {visibleImages.map((image) => (
                    <div key={image.id} className="overflow-hidden rounded-[20px] border border-slate-800/80 bg-slate-950/55">
                      <div className="relative">
                        <img src={image.url} alt="Journal upload" className="h-36 w-full object-cover" loading="lazy" />
                        <span className="absolute left-3 top-3 rounded-full border border-black/20 bg-slate-950/85 px-2.5 py-1 text-[11px] text-slate-200">
                          {formatFileSize(image.byte_size)}
                        </span>
                      </div>
                      <div className="space-y-2.5 border-t border-slate-800/70 px-3 py-3">
                        <div className="flex items-start justify-between gap-3">
                          <p className="min-w-0 truncate text-sm text-slate-200">{image.filename}</p>
                          <Button size="sm" variant="ghost" onClick={() => onDeleteImage(image.id)}>
                            Delete
                          </Button>
                        </div>
                        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
                          <span>{formatTimestamp(image.created_at)}</span>
                          <span>
                            {image.width && image.height ? `${image.width} x ${image.height}` : "Dimensions unavailable"}
                          </span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </section>
      </CardContent>
    </Card>
  );
}
