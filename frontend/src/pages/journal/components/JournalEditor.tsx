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

function formatNumber(value: number) {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatPositionSize(value: number) {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 4,
  }).format(value);
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
}: {
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-slate-800/80 bg-slate-950/35 p-4 shadow-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
          <p className="mt-1 text-sm text-slate-400">{description}</p>
        </div>
        {action}
      </div>
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  );
}

function MetaRow({ label, value, valueClassName }: { label: string; value: string; valueClassName?: string }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl border border-slate-800/70 bg-slate-950/45 px-3 py-2">
      <span className="text-[11px] uppercase tracking-[0.14em] text-slate-500">{label}</span>
      <span className={cn("text-sm text-right text-slate-200", valueClassName)}>{value}</span>
    </div>
  );
}

function StatCard({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "positive" | "negative";
}) {
  return (
    <div className="rounded-xl border border-slate-800/70 bg-slate-950/45 px-3 py-3">
      <p className="text-[11px] uppercase tracking-[0.14em] text-slate-500">{label}</p>
      <p
        className={cn(
          "mt-2 text-base font-semibold",
          tone === "positive" ? "text-emerald-300" : tone === "negative" ? "text-rose-300" : "text-slate-100",
        )}
      >
        {value}
      </p>
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
  const stats = entry.stats_json;
  const netPnl = stats?.net_realized_pnl ?? stats?.net ?? null;
  const notesCharacterCount = draft.body.length;
  const titleCharacterCount = draft.title.length;

  return (
    <Card className="h-full">
      <CardHeader className="border-b border-slate-800/70 pb-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={saveStateDisplay.variant}>{saveStateDisplay.label}</Badge>
              <Badge variant={moodVariant[draft.mood]}>{draft.mood}</Badge>
              {draft.is_archived ? <Badge variant="neutral">Archived</Badge> : <Badge variant="accent">Active</Badge>}
            </div>
            <div>
              <CardTitle className="text-lg md:text-xl">{formatEntryDate(entry.entry_date)}</CardTitle>
              <CardDescription className="mt-1 text-sm">{saveStateDisplay.description}</CardDescription>
            </div>
          </div>

          <div className="rounded-2xl border border-slate-800/70 bg-slate-950/40 px-4 py-3">
            <p className="text-[11px] uppercase tracking-[0.14em] text-slate-500">Entry version</p>
            <p className="mt-1 text-base font-semibold text-slate-100">v{draft.version}</p>
            <p className="mt-1 text-xs text-slate-400">Last updated {formatTimestamp(entry.updated_at)}</p>
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

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.9fr)]">
          <div className="space-y-5">
            <SectionBlock
              title="Entry Details"
              description="Capture the session title, mood, and tags you want to review later."
            >
              <div className="grid gap-4 md:grid-cols-[minmax(0,1.4fr)_minmax(220px,0.6fr)]">
                <label className="block space-y-2">
                  <span className="text-xs font-medium uppercase tracking-[0.14em] text-slate-500">Title</span>
                  <Input
                    value={draft.title}
                    maxLength={160}
                    placeholder="Summarize the session in one line"
                    onChange={(event) => onDraftChange({ ...draft, title: event.target.value })}
                  />
                  <span className="text-xs text-slate-500">{titleCharacterCount}/160 characters</span>
                </label>

                <label className="block space-y-2">
                  <span className="text-xs font-medium uppercase tracking-[0.14em] text-slate-500">Mood</span>
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

              <label className="block space-y-2">
                <span className="text-xs font-medium uppercase tracking-[0.14em] text-slate-500">Tags</span>
                <Input
                  value={draft.tagsInput}
                  maxLength={1024}
                  placeholder="nq, open-drive, patience, execution"
                  onChange={(event) => onDraftChange({ ...draft, tagsInput: event.target.value })}
                />
                <span className="text-xs text-slate-500">
                  Use comma-separated tags for symbol, setup, market context, or execution mistakes.
                </span>
              </label>

              <div className="flex flex-wrap gap-2">
                {normalizedTags.length > 0 ? (
                  normalizedTags.map((tag) => (
                    <span
                      key={tag}
                      className="rounded-full border border-slate-700/80 bg-slate-900/70 px-2.5 py-1 text-xs text-slate-300"
                    >
                      #{tag}
                    </span>
                  ))
                ) : (
                  <p className="text-sm text-slate-500">No tags added yet.</p>
                )}
              </div>
            </SectionBlock>

            <SectionBlock
              title="Notes"
              description="Write your review, execution notes, and follow-up observations for this trading day."
            >
              <label className="block space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-medium uppercase tracking-[0.14em] text-slate-500">Journal Entry</span>
                  <span className="text-xs text-slate-500">{notesCharacterCount}/20,000 characters</span>
                </div>
                <Textarea
                  value={draft.body}
                  maxLength={20000}
                  className="min-h-[320px] resize-y"
                  placeholder="What did the market do, how did you respond, what should you repeat or correct next time?"
                  onChange={(event) => onDraftChange({ ...draft, body: event.target.value })}
                />
              </label>
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800/70 bg-slate-950/45 px-3 py-2 text-sm text-slate-400">
                <p>Autosave runs while you type.</p>
                <p>Paste an image anywhere in this editor to upload it.</p>
              </div>
            </SectionBlock>
          </div>

          <div className="space-y-5">
            <SectionBlock title="Entry Status" description="Track metadata, sync state, and quick entry actions.">
              <div className="space-y-3">
                <MetaRow label="Save state" value={saveStateDisplay.label} />
                <MetaRow label="Created" value={formatTimestamp(entry.created_at)} />
                <MetaRow label="Updated" value={formatTimestamp(entry.updated_at)} />
                <MetaRow label="Stats source" value={entry.stats_source ?? "No trade snapshot yet"} />
                <MetaRow label="Stats pulled" value={formatTimestamp(entry.stats_pulled_at)} />
                <MetaRow label="Images" value={`${images.length}`} />
              </div>

              <div className="flex flex-col gap-2 pt-1 sm:flex-row sm:flex-wrap">
                {saveState === "error" ? (
                  <Button variant="secondary" onClick={onRetrySave} disabled={savingDisabled}>
                    Retry Save
                  </Button>
                ) : null}
                <Button variant="ghost" onClick={onArchiveToggle} disabled={savingDisabled}>
                  {draft.is_archived ? "Unarchive Entry" : "Archive Entry"}
                </Button>
                <Button variant="danger" onClick={onDeleteEntry} disabled={deletingEntry || savingDisabled}>
                  {deletingEntry ? "Deleting..." : "Delete Entry"}
                </Button>
              </div>
            </SectionBlock>

            <SectionBlock
              title="Trade Snapshot"
              description="Pull a fresh trade summary for this journal entry and review the saved metrics."
              action={
                <Button size="sm" variant="secondary" onClick={onPullTradeStats} disabled={pullingStats || savingDisabled}>
                  {pullingStats ? "Pulling..." : stats ? "Refresh Snapshot" : "Pull Trade Stats"}
                </Button>
              }
            >
              {pullStatsError ? (
                <p className="rounded-xl border border-rose-500/35 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
                  {pullStatsError}
                </p>
              ) : null}

              {stats ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <StatCard label="Trade Count" value={String(stats.trade_count)} />
                  <StatCard label="Win Rate" value={`${formatNumber(stats.win_rate)}%`} />
                  <StatCard
                    label="Net Realized PnL"
                    value={netPnl === null ? "Not available" : `${netPnl > 0 ? "+" : ""}${formatNumber(netPnl)}`}
                    tone={netPnl === null ? "default" : netPnl >= 0 ? "positive" : "negative"}
                  />
                  <StatCard label="Total Fees" value={formatNumber(stats.total_fees)} />
                  <StatCard label="Avg Win / Avg Loss" value={`${formatNumber(stats.avg_win)} / ${formatNumber(stats.avg_loss)}`} />
                  <StatCard label="Largest Win / Loss" value={`${formatNumber(stats.largest_win)} / ${formatNumber(stats.largest_loss)}`} />
                  <StatCard
                    label="Largest Position"
                    value={stats.largest_position_size == null ? "-" : formatPositionSize(stats.largest_position_size)}
                  />
                  <StatCard label="Gross / Net" value={`${formatNumber(stats.gross)} / ${formatNumber(stats.net)}`} />
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-700/80 bg-slate-950/35 px-4 py-8 text-center">
                  <p className="text-sm font-medium text-slate-200">No trade snapshot saved.</p>
                  <p className="mt-2 text-sm text-slate-400">
                    Pull trade stats to attach the day&apos;s performance summary to this journal entry.
                  </p>
                </div>
              )}
            </SectionBlock>

            <SectionBlock
              title="Attachments"
              description="Keep chart screenshots and review images with the entry."
              action={
                <>
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
                </>
              }
            >
              <p className="text-sm text-slate-500">Paste directly into the editor or upload a file here.</p>

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
                <div className="rounded-2xl border border-dashed border-slate-700/80 bg-slate-950/35 px-4 py-8 text-center">
                  <p className="text-sm font-medium text-slate-200">No attachments yet.</p>
                  <p className="mt-2 text-sm text-slate-400">Upload or paste a chart image to keep your review in one place.</p>
                </div>
              ) : (
                <div className="grid gap-3 sm:grid-cols-2">
                  {images.map((image) => (
                    <div key={image.id} className="overflow-hidden rounded-2xl border border-slate-800/80 bg-slate-950/55">
                      <img src={image.url} alt="Journal upload" className="h-40 w-full object-cover" loading="lazy" />
                      <div className="space-y-3 px-3 py-3">
                        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
                          <span>{formatFileSize(image.byte_size)}</span>
                          <span>
                            {image.width && image.height ? `${image.width} x ${image.height}` : "Dimensions unavailable"}
                          </span>
                        </div>
                        <div className="flex items-center justify-between gap-3">
                          <p className="truncate text-xs text-slate-500">{image.filename}</p>
                          <Button size="sm" variant="ghost" onClick={() => onDeleteImage(image.id)}>
                            Delete
                          </Button>
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
