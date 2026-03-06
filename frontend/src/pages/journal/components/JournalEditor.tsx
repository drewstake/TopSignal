import { type ChangeEvent, type ClipboardEvent, type ReactNode, useRef } from "react";

import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import { Textarea } from "../../../components/ui/Textarea";
import { cn } from "../../../components/ui/cn";
import type { JournalEntry, JournalEntryImage, JournalMood } from "../../../lib/types";
import { handleClipboardImagePaste } from "../journalClipboard";
import type { JournalSaveState } from "../journalAutosave";
import { parseTagsInput, type JournalDraft } from "../journalUtils";

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
  onUploadImage: (file: File | Blob) => void;
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

function SectionBlock({
  title,
  description,
  action,
  children,
  className,
  contentClassName,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  return (
    <section
      className={cn(
        "overflow-hidden rounded-[22px] border border-slate-800/80 bg-gradient-to-b from-slate-950/80 via-slate-950/45 to-slate-950/30 shadow-panel",
        className,
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800/70 px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
          <p className="mt-1 text-[11px] text-slate-400">{description}</p>
        </div>
        {action}
      </div>
      <div className={cn("space-y-3 px-4 py-4", contentClassName)}>{children}</div>
    </section>
  );
}

function FieldShell({
  label,
  hint,
  meta,
  children,
  className,
}: {
  label: string;
  hint?: string;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("block rounded-xl border border-slate-800/75 bg-slate-950/45 p-3.5", className)}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">{label}</span>
          {hint ? <p className="mt-1 text-[11px] text-slate-400">{hint}</p> : null}
        </div>
        {meta ? <div className="text-[11px] text-slate-500">{meta}</div> : null}
      </div>
      <div className="mt-2.5 space-y-2.5">{children}</div>
    </label>
  );
}

function MetaRow({ label, value, valueClassName }: { label: string; value: string; valueClassName?: string }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl border border-slate-800/70 bg-slate-950/45 px-3 py-2.5">
      <span className="text-[11px] uppercase tracking-[0.14em] text-slate-500">{label}</span>
      <span className={cn("text-right text-xs text-slate-200 sm:text-sm", valueClassName)}>{value}</span>
    </div>
  );
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
  deletingEntry,
  onDraftChange,
  onArchiveToggle,
  onRetrySave,
  onReloadServerVersion,
  onUploadImage,
  onDeleteImage,
  onDeleteEntry,
}: JournalEditorProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
              Pick a journal entry to edit notes, review trade stats, and manage attachments.
            </p>
          </div>
        </CardContent>
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

  const saveStateDisplay = saveStateMeta[saveState];
  const normalizedTags = parseTagsInput(draft.tagsInput);
  const notesCharacterCount = draft.body.length;
  const titleCharacterCount = draft.title.length;
  const draftTitleDisplay = draft.title.trim() || "Untitled entry";
  const imageSummary = `${images.length} ${images.length === 1 ? "image" : "images"}`;

  return (
    <Card className="h-full overflow-hidden">
      <CardHeader className="space-y-4 border-b border-slate-800/70 pb-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <p className="text-[11px] uppercase tracking-[0.18em] text-slate-500">{formatEntryDate(entry.entry_date)}</p>
            <CardTitle className="mt-1.5 text-xl md:text-2xl">{draftTitleDisplay}</CardTitle>
            <CardDescription className="mt-2 max-w-2xl text-sm">{saveStateDisplay.description}</CardDescription>
          </div>

          <div className="flex flex-wrap items-center gap-2 md:justify-end">
            <Badge variant={saveStateDisplay.variant}>{saveStateDisplay.label}</Badge>
            <Badge variant={moodVariant[draft.mood]}>{draft.mood}</Badge>
            {draft.is_archived ? <Badge variant="neutral">Archived</Badge> : <Badge variant="accent">Active</Badge>}
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-5 pt-5" onPaste={handlePaste}>
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

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.42fr)_minmax(320px,0.9fr)]">
          <div className="space-y-5">
            <SectionBlock
              title="Entry Details"
              description="Title, mood, and tags."
              contentClassName="space-y-2.5"
            >
              <div className="grid gap-3 xl:grid-cols-[minmax(0,1.35fr)_180px_minmax(260px,1fr)] xl:items-end">
                <label className="block space-y-1.5">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">Title</span>
                    <span className="text-[11px] text-slate-500">{titleCharacterCount}/160</span>
                  </div>
                  <Input
                    value={draft.title}
                    maxLength={160}
                    placeholder="Summarize the session in one line"
                    className="text-sm"
                    onChange={(event) => onDraftChange({ ...draft, title: event.target.value })}
                  />
                </label>

                <label className="block space-y-1.5">
                  <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">Mood</span>
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

                <label className="block space-y-1.5">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">Tags</span>
                    {normalizedTags.length > 0 ? (
                      <span className="text-[11px] text-slate-500">{normalizedTags.length} total</span>
                    ) : null}
                  </div>
                  <Input
                    value={draft.tagsInput}
                    maxLength={1024}
                    placeholder="nq, open-drive, patience, execution"
                    onChange={(event) => onDraftChange({ ...draft, tagsInput: event.target.value })}
                  />
                </label>
              </div>

              {normalizedTags.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {normalizedTags.map((tag) => (
                    <span
                      key={tag}
                      className="rounded-full border border-slate-700/80 bg-slate-900/70 px-2 py-0.5 text-[10px] text-slate-300"
                    >
                      #{tag}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-[11px] text-slate-500">No tags added yet.</p>
              )}
            </SectionBlock>

            <SectionBlock
              title="Notes"
              description="Write your review, execution notes, and follow-up observations for this trading day."
            >
              <FieldShell
                label="Journal Entry"
                hint="Capture market context, execution choices, mistakes, and what you want to repeat next time."
                meta={`${notesCharacterCount}/20,000 characters`}
                className="bg-slate-950/35"
              >
                <Textarea
                  value={draft.body}
                  maxLength={20000}
                  className="min-h-[280px] resize-y border-slate-800 bg-slate-950/70 leading-6"
                  placeholder="What did the market do, how did you respond, what should you repeat or correct next time?"
                  onChange={(event) => onDraftChange({ ...draft, body: event.target.value })}
                />
              </FieldShell>

              <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-800/70 bg-slate-950/45 px-3 py-2 text-[11px] text-slate-400">
                <span>Autosave runs while you type.</span>
                <span>Paste an image in the editor to upload it.</span>
              </div>
            </SectionBlock>
          </div>

          <div className="space-y-5">
            <SectionBlock title="Entry Status" description="Timestamps and actions." contentClassName="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <MetaRow label="Created" value={formatTimestamp(entry.created_at)} />
                <MetaRow label="Updated" value={formatTimestamp(entry.updated_at)} />
              </div>

              <div className="rounded-xl border border-slate-800/75 bg-slate-950/45 px-3 py-2.5">
                <div className="flex flex-wrap gap-2">
                  {saveState === "error" ? (
                    <Button size="sm" variant="secondary" onClick={onRetrySave} disabled={savingDisabled}>
                      Retry Save
                    </Button>
                  ) : null}
                  <Button size="sm" variant="ghost" onClick={onArchiveToggle} disabled={savingDisabled}>
                    {draft.is_archived ? "Unarchive Entry" : "Archive Entry"}
                  </Button>
                  <Button size="sm" variant="danger" onClick={onDeleteEntry} disabled={deletingEntry || savingDisabled}>
                    {deletingEntry ? "Deleting..." : "Delete Entry"}
                  </Button>
                </div>
              </div>
            </SectionBlock>

            <SectionBlock
              title="Attachments"
              description="Screenshots and review images."
              action={
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-full border border-slate-700/80 bg-slate-900/60 px-3 py-1 text-[11px] uppercase tracking-[0.14em] text-slate-300">
                    {imageSummary}
                  </span>
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
              }
            >
              <p className="text-[11px] text-slate-400">Paste into notes or use the file picker.</p>

              {imagesError ? (
                <p className="rounded-xl border border-rose-500/35 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
                  {imagesError}
                </p>
              ) : null}

              {imagesLoading ? (
                <div className="rounded-2xl border border-slate-800/70 bg-slate-950/45 px-4 py-8 text-center text-sm text-slate-400">
                  Loading images...
                </div>
              ) : images.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-slate-700/80 bg-slate-950/35 px-4 py-6 text-center">
                  <p className="text-sm font-medium text-slate-200">No attachments yet.</p>
                  <p className="mt-1 text-sm text-slate-400">Upload or paste a chart image here.</p>
                </div>
              ) : (
                <div className="grid gap-3 sm:grid-cols-2">
                  {images.map((image) => (
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
              )}
            </SectionBlock>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
