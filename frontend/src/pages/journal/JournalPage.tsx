import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import {
  ACCOUNT_QUERY_PARAM,
  parseAccountId,
  readStoredAccountId,
  readStoredMainAccountId,
  writeStoredAccountId,
} from "../../lib/accountSelection";
import { accountsApi } from "../../lib/api";
import { sortAccountsForSelection } from "../../lib/accountOrdering";
import { getTradingDayBoundaryIso } from "../../lib/tradingDay";
import type {
  AccountInfo,
  JournalEntry,
  JournalEntryImage,
  JournalEntryUpdateInput,
} from "../../lib/types";
import { DebouncedAutosaveQueue, type JournalSaveState } from "./journalAutosave";
import { copyTextToClipboard } from "./journalClipboard";
import { buildJournalCopyText, buildJournalCopyTradeStats, getCopyEntry, type JournalCopyTradeStats } from "./journalCopy";
import { JournalEditor } from "./components/JournalEditor";
import { JournalCopyActions, type JournalCopyAction } from "./components/JournalCopyActions";
import { JournalList } from "./components/JournalList";
import { getVersionConflictServerEntry } from "./journalConflict";
import {
  applyJournalSaveResultToDraft,
  applyJournalSaveResultToEntry,
  buildJournalQuery,
  draftToUpdatePayload,
  entryToDraft,
  getTodayTradingDateIso,
  hasJournalTradeStatsSnapshot,
  JOURNAL_AUTOSAVE_DELAY_MS,
  JOURNAL_PAGE_SIZE,
  parseTagsInput,
  reconcileDraftWithServerEntry,
  type JournalDraft,
  type JournalMoodFilter,
} from "./journalUtils";

const JOURNAL_DATE_QUERY_PARAM = "date";
const COPY_TOAST_DURATION_MS = 2600;

type JournalAutosavePatch = Omit<JournalEntryUpdateInput, "version">;

interface QueuedJournalSave {
  accountId: number;
  entryId: number;
  patch: JournalAutosavePatch;
}

interface JournalToastState {
  id: number;
  message: string;
  tone: "success" | "error";
}

function parseJournalDateParam(value: string | null): string | null {
  if (!value) {
    return null;
  }
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function draftToAutosavePatch(draft: JournalDraft): JournalAutosavePatch {
  return {
    title: draft.title,
    mood: draft.mood,
    tags: parseTagsInput(draft.tagsInput),
    body: draftToUpdatePayload(draft).body,
    is_archived: draft.is_archived,
  };
}

function journalAutosavePatchEquals(left: JournalAutosavePatch, right: JournalAutosavePatch): boolean {
  if (
    left.title !== right.title ||
    left.mood !== right.mood ||
    left.body !== right.body ||
    left.is_archived !== right.is_archived
  ) {
    return false;
  }

  const leftTags = left.tags ?? [];
  const rightTags = right.tags ?? [];
  if (leftTags.length !== rightTags.length) {
    return false;
  }
  return leftTags.every((tag, index) => tag === rightTags[index]);
}

function toQueuedJournalSave(accountId: number, entryId: number, draft: JournalDraft): QueuedJournalSave {
  return {
    accountId,
    entryId,
    patch: draftToAutosavePatch(draft),
  };
}

function queuedJournalSaveEquals(left: QueuedJournalSave, right: QueuedJournalSave): boolean {
  return (
    left.accountId === right.accountId &&
    left.entryId === right.entryId &&
    journalAutosavePatchEquals(left.patch, right.patch)
  );
}

function upsertEntry(entries: JournalEntry[], nextEntry: JournalEntry): JournalEntry[] {
  const existingIndex = entries.findIndex((entry) => entry.id === nextEntry.id);
  if (existingIndex === -1) {
    return [nextEntry, ...entries];
  }
  return entries.map((entry) => (entry.id === nextEntry.id ? nextEntry : entry));
}

function restoreImageAtIndex(
  images: JournalEntryImage[],
  image: JournalEntryImage,
  targetIndex: number,
): JournalEntryImage[] {
  if (images.some((currentImage) => currentImage.id === image.id)) {
    return images;
  }

  const nextImages = images.slice();
  nextImages.splice(Math.min(targetIndex, nextImages.length), 0, image);
  return nextImages;
}

function FilterField({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={`flex min-w-0 items-center gap-1 whitespace-nowrap ${className ?? ""}`}>
      <span className="shrink-0 text-[10px] uppercase tracking-[0.12em] text-slate-500">{label}</span>
      {children}
    </label>
  );
}

function InlineMessage({ tone, children }: { tone: "error" | "info"; children: ReactNode }) {
  const classes =
    tone === "error"
      ? "border-rose-500/35 bg-rose-500/10 text-rose-200"
      : "border-amber-500/35 bg-amber-500/10 text-amber-200";

  return (
    <div className={`rounded-2xl border px-4 py-3 text-sm ${classes}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

function JournalListSkeleton() {
  return (
    <Card className="h-full xl:flex xl:min-h-0 xl:flex-col">
      <CardHeader>
        <CardTitle>Journal Entries</CardTitle>
        <CardDescription>Loading your recent entries and filters.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 xl:flex-1 xl:overflow-hidden">
        {Array.from({ length: 5 }).map((_, index) => (
          <div
            key={index}
            className="rounded-2xl border border-slate-800/80 bg-slate-950/40 px-4 py-4"
            aria-hidden="true"
          >
            <div className="h-3 w-24 rounded bg-slate-800/80" />
            <div className="mt-3 h-4 w-2/3 rounded bg-slate-800/80" />
            <div className="mt-4 h-3 w-full rounded bg-slate-800/70" />
            <div className="mt-2 h-3 w-5/6 rounded bg-slate-800/60" />
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function CopyToast({ toast }: { toast: JournalToastState }) {
  const classes =
    toast.tone === "error"
      ? "border-rose-500/45 bg-slate-950/95 text-rose-100"
      : "border-cyan-400/45 bg-slate-950/95 text-slate-50";

  return (
    <div
      className={`pointer-events-auto fixed bottom-6 right-6 z-30 max-w-sm rounded-2xl border px-4 py-3 text-sm shadow-lg ${classes}`}
      role={toast.tone === "error" ? "alert" : "status"}
      aria-live={toast.tone === "error" ? "assertive" : "polite"}
    >
      {toast.message}
    </div>
  );
}

function applyDraftToEntry(entry: JournalEntry, selectedId: number | null, draft: JournalDraft | null): JournalEntry {
  if (!draft || selectedId === null || entry.id !== selectedId) {
    return entry;
  }

  return {
    ...entry,
    title: draft.title,
    mood: draft.mood,
    tags: parseTagsInput(draft.tagsInput),
    body: draft.body,
    version: draft.version,
    is_archived: draft.is_archived,
  };
}

function getCopyFailureMessage(error: unknown) {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "Couldn't copy journal text. Please try again.";
}

export function JournalPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));
  const dateFromQuery = parseJournalDateParam(searchParams.get(JOURNAL_DATE_QUERY_PARAM));

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [totalEntries, setTotalEntries] = useState(0);
  const [loadingEntries, setLoadingEntries] = useState(false);
  const [entriesError, setEntriesError] = useState<string | null>(null);
  const [entriesInfo, setEntriesInfo] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draft, setDraft] = useState<JournalDraft | null>(null);
  const [saveState, setSaveState] = useState<JournalSaveState>("saved");
  const [creatingEntry, setCreatingEntry] = useState(false);
  const [deletingEntry, setDeletingEntry] = useState(false);

  const [images, setImages] = useState<JournalEntryImage[]>([]);
  const [imagesLoading, setImagesLoading] = useState(false);
  const [imagesError, setImagesError] = useState<string | null>(null);
  const [uploadingImage, setUploadingImage] = useState(false);

  const [conflictServerEntry, setConflictServerEntry] = useState<JournalEntry | null>(null);
  const [copyAction, setCopyAction] = useState<JournalCopyAction | null>(null);
  const [copyToast, setCopyToast] = useState<JournalToastState | null>(null);

  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [moodFilter, setMoodFilter] = useState<JournalMoodFilter>("ALL");
  const [includeArchived] = useState(false);
  const [page, setPage] = useState(1);

  const autosaveRef = useRef<DebouncedAutosaveQueue<QueuedJournalSave> | null>(null);
  const selectedEntryIdRef = useRef<number | null>(null);
  const selectedAccountIdRef = useRef<number | null>(null);
  const selectedEntryVersionRef = useRef<number | null>(null);
  const draftRef = useRef<JournalDraft | null>(null);
  const draftEntryIdRef = useRef<number | null>(null);
  const imagesRef = useRef<JournalEntryImage[]>([]);
  const includeArchivedRef = useRef(includeArchived);
  const handledDateKeyRef = useRef<string | null>(null);
  const entriesRequestVersionRef = useRef(0);
  const imagesRequestVersionRef = useRef(0);
  const copyStatsByDateRef = useRef<Map<string, Promise<JournalCopyTradeStats | null>>>(new Map());
  const statsPullInFlightRef = useRef<Set<string>>(new Set());

  imagesRef.current = images;
  includeArchivedRef.current = includeArchived;

  const setActiveAccount = useCallback(
    (accountId: number) => {
      const next = new URLSearchParams(searchParams);
      next.set(ACCOUNT_QUERY_PARAM, String(accountId));
      setSearchParams(next, { replace: true });
      writeStoredAccountId(accountId);
    },
    [searchParams, setSearchParams],
  );

  const loadAccounts = useCallback(async () => {
    try {
      const payload = await accountsApi.getSelectableAccounts();
      setAccounts(payload);
    } catch {
      setAccounts([]);
    }
  }, []);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  const orderedAccounts = useMemo(() => sortAccountsForSelection(accounts), [accounts]);

  useEffect(() => {
    if (orderedAccounts.length === 0) {
      return;
    }

    if (accountFromQuery && orderedAccounts.some((account) => account.id === accountFromQuery)) {
      writeStoredAccountId(accountFromQuery);
      return;
    }

    const persistedMainAccountId = orderedAccounts.find((account) => account.is_main)?.id ?? null;
    if (persistedMainAccountId) {
      setActiveAccount(persistedMainAccountId);
      return;
    }

    const storedMainAccountId = readStoredMainAccountId();
    if (storedMainAccountId && orderedAccounts.some((account) => account.id === storedMainAccountId)) {
      setActiveAccount(storedMainAccountId);
      return;
    }

    const storedAccountId = readStoredAccountId();
    if (storedAccountId && orderedAccounts.some((account) => account.id === storedAccountId)) {
      setActiveAccount(storedAccountId);
      return;
    }

    setActiveAccount(orderedAccounts[0].id);
  }, [orderedAccounts, accountFromQuery, setActiveAccount]);

  const selectedAccount = useMemo(
    () => orderedAccounts.find((account) => account.id === accountFromQuery) ?? null,
    [orderedAccounts, accountFromQuery],
  );
  const selectedAccountId = selectedAccount?.id ?? null;
  selectedAccountIdRef.current = selectedAccountId;

  const totalPages = Math.max(1, Math.ceil(totalEntries / JOURNAL_PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const offset = (currentPage - 1) * JOURNAL_PAGE_SIZE;

  const listQuery = useMemo(
    () =>
      buildJournalQuery({
        startDate,
        endDate,
        mood: moodFilter,
        queryText: "",
        includeArchived,
        limit: JOURNAL_PAGE_SIZE,
        offset,
      }),
    [endDate, includeArchived, moodFilter, offset, startDate],
  );

  useEffect(() => {
    setPage(1);
  }, [startDate, endDate, moodFilter, includeArchived]);

  const selectedEntry = useMemo(
    () => entries.find((entry) => entry.id === selectedId) ?? null,
    [entries, selectedId],
  );
  selectedEntryIdRef.current = selectedEntry?.id ?? null;

  const showCopyToast = useCallback((tone: JournalToastState["tone"], message: string) => {
    setCopyToast({
      id: Date.now(),
      tone,
      message,
    });
  }, []);

  useEffect(() => {
    if (!copyToast) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setCopyToast((currentToast) => (currentToast?.id === copyToast.id ? null : currentToast));
    }, COPY_TOAST_DURATION_MS);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [copyToast]);

  useEffect(() => {
    copyStatsByDateRef.current.clear();
  }, [selectedAccountId]);

  useEffect(() => {
    statsPullInFlightRef.current.clear();
  }, [selectedAccountId]);

  const flushAutosave = useCallback(async () => {
    if (!autosaveRef.current) {
      return;
    }
    await autosaveRef.current.flush();
  }, []);

  const loadEntries = useCallback(async (signal?: AbortSignal) => {
    const requestVersion = entriesRequestVersionRef.current + 1;
    entriesRequestVersionRef.current = requestVersion;
    if (!selectedAccountId) {
      setEntries([]);
      setTotalEntries(0);
      setLoadingEntries(false);
      setEntriesError(null);
      setSelectedId(null);
      return;
    }

    setLoadingEntries(true);
    setEntriesError(null);

    try {
      await flushAutosave();
      if (signal?.aborted || requestVersion !== entriesRequestVersionRef.current) {
        return;
      }
      const payload = await accountsApi.getJournalEntries(selectedAccountId, listQuery, { signal });
      if (requestVersion !== entriesRequestVersionRef.current) {
        return;
      }
      setEntries(payload.items);
      setTotalEntries(payload.total);
      setSelectedId((currentSelectedId) => {
        if (currentSelectedId && payload.items.some((entry) => entry.id === currentSelectedId)) {
          return currentSelectedId;
        }
        if (dateFromQuery) {
          const dateMatchedEntry = payload.items.find((entry) => entry.entry_date === dateFromQuery);
          if (dateMatchedEntry) {
            return dateMatchedEntry.id;
          }
        }
        return payload.items[0]?.id ?? null;
      });
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      if (requestVersion !== entriesRequestVersionRef.current) {
        return;
      }
      setEntries([]);
      setTotalEntries(0);
      setSelectedId(null);
      setEntriesError(err instanceof Error ? err.message : "Failed to load journal entries");
    } finally {
      if (requestVersion === entriesRequestVersionRef.current) {
        setLoadingEntries(false);
      }
    }
  }, [dateFromQuery, flushAutosave, listQuery, selectedAccountId]);

  useEffect(() => {
    const controller = new AbortController();
    void loadEntries(controller.signal);
    return () => {
      controller.abort();
    };
  }, [loadEntries]);

  useEffect(() => {
    if (page <= totalPages) {
      return;
    }
    setPage(totalPages);
  }, [page, totalPages]);

  useEffect(() => {
    const queue = new DebouncedAutosaveQueue<QueuedJournalSave>({
      delayMs: JOURNAL_AUTOSAVE_DELAY_MS,
      equals: queuedJournalSaveEquals,
      onStateChange: setSaveState,
      save: async (payload) => {
        const expectedVersion = selectedEntryVersionRef.current;
        if (!expectedVersion) {
          return;
        }

        const updated = await accountsApi.updateJournalEntry(payload.accountId, payload.entryId, {
          ...payload.patch,
          version: expectedVersion,
        });

        setConflictServerEntry(null);
        setEntries((currentEntries) => {
          if (updated.is_archived && !includeArchivedRef.current) {
            return currentEntries.filter((entry) => entry.id !== updated.id);
          }
          let changed = false;
          const nextEntries = currentEntries.map((entry) => {
            if (entry.id !== updated.id) {
              return entry;
            }
            changed = true;
            return applyJournalSaveResultToEntry({
              entry,
              patch: payload.patch,
              result: updated,
            });
          });
          return changed ? nextEntries : currentEntries;
        });
        setTotalEntries((currentTotal) =>
          updated.is_archived && !includeArchivedRef.current ? Math.max(0, currentTotal - 1) : currentTotal,
        );

        if (
          selectedAccountIdRef.current === updated.account_id &&
          selectedEntryIdRef.current === updated.id
        ) {
          selectedEntryVersionRef.current = updated.version;
        }

        const currentDraft = draftRef.current;
        if (!currentDraft) {
          return;
        }

        const currentPatch = draftToAutosavePatch(currentDraft);
        if (journalAutosavePatchEquals(currentPatch, payload.patch)) {
          const normalizedDraft = applyJournalSaveResultToDraft({
            draft: currentDraft,
            patch: payload.patch,
            result: updated,
          });
          setDraft(normalizedDraft);
          draftRef.current = normalizedDraft;
          return;
        }

        if (currentDraft.version === expectedVersion) {
          const nextDraft = {
            ...currentDraft,
            version: updated.version,
          };
          setDraft(nextDraft);
          draftRef.current = nextDraft;
        }
      },
      onError: (error) => {
        const serverEntry = getVersionConflictServerEntry(error);
        if (!serverEntry) {
          return;
        }
        setConflictServerEntry(serverEntry);
      },
    });

    autosaveRef.current = queue;
    return () => {
      void queue.flush();
      queue.dispose();
      autosaveRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!selectedEntry) {
      setDraft(null);
      draftRef.current = null;
      draftEntryIdRef.current = null;
      selectedEntryVersionRef.current = null;
      setSaveState("saved");
      setConflictServerEntry(null);
      return;
    }

    const { nextDraft, replaceBaseline } = reconcileDraftWithServerEntry({
      currentDraft: draftRef.current,
      currentEntryId: draftEntryIdRef.current,
      serverEntry: selectedEntry,
    });

    setDraft(nextDraft);
    draftRef.current = nextDraft;
    draftEntryIdRef.current = selectedEntry.id;
    selectedEntryVersionRef.current = selectedEntry.version;
    setConflictServerEntry(null);
    if (!selectedAccountId || !replaceBaseline) {
      return;
    }
    autosaveRef.current?.setBaseline(toQueuedJournalSave(selectedAccountId, selectedEntry.id, nextDraft));
  }, [selectedAccountId, selectedEntry]);

  useEffect(() => {
    if (!selectedAccountId || !selectedEntry || hasJournalTradeStatsSnapshot(selectedEntry)) {
      return;
    }

    const requestKey = `${selectedAccountId}:${selectedEntry.id}`;
    if (statsPullInFlightRef.current.has(requestKey)) {
      return;
    }

    statsPullInFlightRef.current.add(requestKey);

    void accountsApi
      .pullJournalTradeStats(selectedAccountId, selectedEntry.id)
      .then((updatedEntry) => {
        setEntries((currentEntries) =>
          currentEntries.some((entry) => entry.id === updatedEntry.id)
            ? currentEntries.map((entry) => (entry.id === updatedEntry.id ? updatedEntry : entry))
            : currentEntries,
        );
      })
      .catch(() => {
        // Keep the entry visible as-is when the snapshot request fails.
      })
      .finally(() => {
        statsPullInFlightRef.current.delete(requestKey);
      });
  }, [selectedAccountId, selectedEntry]);

  useEffect(() => {
    return () => {
      void flushAutosave();
    };
  }, [flushAutosave, selectedAccountId]);

  const loadEntryImages = useCallback(async (signal?: AbortSignal) => {
    const requestVersion = imagesRequestVersionRef.current + 1;
    imagesRequestVersionRef.current = requestVersion;
    if (!selectedAccountId || !selectedEntry?.id) {
      setImages([]);
      setImagesLoading(false);
      setImagesError(null);
      return;
    }

    setImagesLoading(true);
    setImagesError(null);

    try {
      const rows = await accountsApi.listJournalImages(selectedAccountId, selectedEntry.id, { signal });
      if (requestVersion !== imagesRequestVersionRef.current) {
        return;
      }
      setImages(rows);
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      if (requestVersion !== imagesRequestVersionRef.current) {
        return;
      }
      setImages([]);
      setImagesError(err instanceof Error ? err.message : "Failed to load images");
    } finally {
      if (requestVersion === imagesRequestVersionRef.current) {
        setImagesLoading(false);
      }
    }
  }, [selectedAccountId, selectedEntry?.id]);

  useEffect(() => {
    const controller = new AbortController();
    void loadEntryImages(controller.signal);
    return () => {
      controller.abort();
    };
  }, [loadEntryImages]);

  const commitDraft = useCallback((nextDraft: JournalDraft) => {
    setDraft(nextDraft);
    draftRef.current = nextDraft;

    const accountId = selectedAccountIdRef.current;
    const entryId = selectedEntryIdRef.current;
    if (!accountId || !entryId) {
      return;
    }

    autosaveRef.current?.queue(toQueuedJournalSave(accountId, entryId, nextDraft));
  }, []);

  const handleDraftChange = useCallback(
    (nextDraft: JournalDraft) => {
      commitDraft(nextDraft);
    },
    [commitDraft],
  );

  const handleSelectEntry = useCallback(
    async (entryId: number) => {
      await flushAutosave();
      setSelectedId(entryId);
    },
    [flushAutosave],
  );

  const handleCreateEntry = useCallback(
    async (entryDate?: string) => {
      if (!selectedAccountId) {
        return;
      }

      setCreatingEntry(true);
      setEntriesError(null);
      setEntriesInfo(null);
      try {
        await flushAutosave();
        const created = await accountsApi.createJournalEntry(selectedAccountId, {
          entry_date: entryDate ?? getTodayTradingDateIso(),
          title: "New Entry",
          mood: "Neutral",
          tags: [],
          body: "",
        });
        // Invalidate any pending entry-list response started before this mutation.
        entriesRequestVersionRef.current += 1;
        setLoadingEntries(false);
        setEntries((currentEntries) => upsertEntry(currentEntries, created));
        setTotalEntries((currentTotal) => (created.already_existed ? currentTotal : currentTotal + 1));
        setPage(1);
        setSelectedId(created.id);
        setConflictServerEntry(null);
        setEntriesInfo(created.already_existed ? `An entry already exists for ${created.entry_date}. Opened that entry.` : null);
        if (currentPage === 1) {
          void loadEntries();
        }
      } catch (err) {
        setEntriesInfo(null);
        setEntriesError(err instanceof Error ? err.message : "Failed to create journal entry");
      } finally {
        setCreatingEntry(false);
      }
    },
    [currentPage, flushAutosave, loadEntries, selectedAccountId],
  );

  useEffect(() => {
    const dateKey = selectedAccountId && dateFromQuery ? `${selectedAccountId}:${dateFromQuery}` : null;
    if (!dateKey) {
      handledDateKeyRef.current = null;
      return;
    }
    if (handledDateKeyRef.current === dateKey) {
      return;
    }
    if (!selectedAccountId || loadingEntries) {
      return;
    }

    handledDateKeyRef.current = dateKey;
    const existing = entries.find((entry) => entry.entry_date === dateFromQuery);
    if (existing) {
      setSelectedId(existing.id);
      return;
    }

    void handleCreateEntry(dateFromQuery ?? undefined);
  }, [dateFromQuery, entries, handleCreateEntry, loadingEntries, selectedAccountId]);

  const handleArchiveToggle = useCallback(async () => {
    if (!draftRef.current || !autosaveRef.current) {
      return;
    }
    const nextDraft = {
      ...draftRef.current,
      is_archived: !draftRef.current.is_archived,
    };
    setDraft(nextDraft);
    draftRef.current = nextDraft;
    const accountId = selectedAccountIdRef.current;
    const entryId = selectedEntryIdRef.current;
    if (!accountId || !entryId) {
      return;
    }
    autosaveRef.current.queue(toQueuedJournalSave(accountId, entryId, nextDraft));
    await autosaveRef.current.retryNow();
    if (!includeArchivedRef.current && nextDraft.is_archived) {
      setSelectedId((currentSelectedId) => {
        if (currentSelectedId !== selectedEntryIdRef.current) {
          return currentSelectedId;
        }
        return entries.find((entry) => entry.id !== currentSelectedId)?.id ?? null;
      });
    }
  }, [entries]);

  const handleRetrySave = useCallback(async () => {
    if (!autosaveRef.current) {
      return;
    }
    await autosaveRef.current.retryNow();
  }, []);

  const handleReloadServerVersion = useCallback(() => {
    if (!conflictServerEntry || !selectedAccountId) {
      return;
    }

    setEntries((currentEntries) => upsertEntry(currentEntries, conflictServerEntry));
    selectedEntryVersionRef.current = conflictServerEntry.version;

    const nextDraft = entryToDraft(conflictServerEntry);
    setDraft(nextDraft);
    draftRef.current = nextDraft;
    setSelectedId(conflictServerEntry.id);
    autosaveRef.current?.setBaseline(toQueuedJournalSave(selectedAccountId, conflictServerEntry.id, nextDraft));
    setSaveState("saved");
    setConflictServerEntry(null);
  }, [conflictServerEntry, selectedAccountId]);

  const uploadJournalImageFile = useCallback(
    async (file: File | Blob, context: { accountId: number; entryId: number }): Promise<JournalEntryImage | null> => {
      const { accountId, entryId } = context;
      if (!accountId || !entryId) {
        return null;
      }

      setUploadingImage(true);
      setImagesError(null);
      try {
        const uploaded = await accountsApi.uploadJournalImage(accountId, entryId, file);
        if (selectedAccountIdRef.current === accountId && selectedEntryIdRef.current === entryId) {
          setImages((current) => [...current, uploaded]);
        }
        return uploaded;
      } catch (err) {
        setImagesError(err instanceof Error ? err.message : "Failed to upload image");
        return null;
      } finally {
        setUploadingImage(false);
      }
    },
    [],
  );

  const handlePasteImage = useCallback(
    async (file: File, selection: { start: number; end: number }) => {
      void selection;
      const accountId = selectedAccountIdRef.current;
      const entryId = selectedEntryIdRef.current;
      if (!accountId || !entryId) {
        return;
      }

      const uploaded = await uploadJournalImageFile(file, { accountId, entryId });
      if (!uploaded || !entryId || draftEntryIdRef.current !== entryId || !draftRef.current) {
        return;
      }
    },
    [uploadJournalImageFile],
  );

  const handleDeleteImage = useCallback(
    async (imageId: number) => {
      const accountId = selectedAccountIdRef.current;
      const entryId = selectedEntryIdRef.current;
      if (!accountId || !entryId) {
        return;
      }

      const imageIndex = imagesRef.current.findIndex((image) => image.id === imageId);
      if (imageIndex === -1) {
        return;
      }

      const imageToDelete = imagesRef.current[imageIndex];
      setImagesError(null);
      setImages((current) => current.filter((image) => image.id !== imageId));

      try {
        await accountsApi.deleteJournalImage(accountId, entryId, imageId);
      } catch (err) {
        if (selectedAccountIdRef.current === accountId && selectedEntryIdRef.current === entryId) {
          setImages((current) => restoreImageAtIndex(current, imageToDelete, imageIndex));
        }
        setImagesError(err instanceof Error ? err.message : "Failed to delete image");
      }
    },
    [],
  );

  const handleDeleteEntry = useCallback(async () => {
    if (!selectedAccountId || !selectedEntry) {
      return;
    }

    const confirmed = window.confirm("Delete this entry permanently?");
    if (!confirmed) {
      return;
    }

    setDeletingEntry(true);
    setEntriesError(null);

    try {
      await flushAutosave();
      await accountsApi.deleteJournalEntry(selectedAccountId, selectedEntry.id);

      setEntries((currentEntries) => currentEntries.filter((entry) => entry.id !== selectedEntry.id));
      setTotalEntries((currentTotal) => Math.max(0, currentTotal - 1));
      setSelectedId((currentSelectedId) => {
        if (currentSelectedId !== selectedEntry.id) {
          return currentSelectedId;
        }
        return entries.find((entry) => entry.id !== selectedEntry.id)?.id ?? null;
      });
      setDraft(null);
      draftRef.current = null;
      setImages([]);
      setConflictServerEntry(null);
    } catch (err) {
      setEntriesError(err instanceof Error ? err.message : "Failed to delete journal entry");
    } finally {
      setDeletingEntry(false);
    }
  }, [entries, flushAutosave, selectedAccountId, selectedEntry]);

  const loadCopyTradeStatsForDay = useCallback(
    (entry: JournalEntry) => {
      if (!selectedAccountId) {
        return Promise.resolve(buildJournalCopyTradeStats({ entry }));
      }

      const dayStart = getTradingDayBoundaryIso(entry.entry_date, false);
      const dayEnd = getTradingDayBoundaryIso(entry.entry_date, true);
      if (!dayStart || !dayEnd) {
        return Promise.resolve(buildJournalCopyTradeStats({ entry }));
      }

      const cacheKey = `${selectedAccountId}:${entry.entry_date}`;
      const cached = copyStatsByDateRef.current.get(cacheKey);
      if (cached) {
        return cached;
      }

      const request = Promise.allSettled([
        accountsApi.getSummary(selectedAccountId, {
          start: dayStart,
          end: dayEnd,
        }),
        accountsApi.getTrades(selectedAccountId, {
          limit: 1000,
          start: dayStart,
          end: dayEnd,
        }),
      ]).then(([summaryResult, tradesResult]) =>
        buildJournalCopyTradeStats({
          entry,
          summary: summaryResult.status === "fulfilled" ? summaryResult.value : null,
          trades: tradesResult.status === "fulfilled" ? tradesResult.value : [],
        }),
      );

      copyStatsByDateRef.current.set(cacheKey, request);
      return request;
    },
    [selectedAccountId],
  );

  const loadTradeStatsByDate = useCallback(
    async (entriesToCopy: JournalEntry[]) => {
      const uniqueEntriesByDate = new Map(entriesToCopy.map((entry) => [entry.entry_date, entry]));
      const statsEntries = await Promise.all(
        Array.from(uniqueEntriesByDate.entries()).map(async ([entryDate, entry]) => [
          entryDate,
          await loadCopyTradeStatsForDay(entry),
        ] as const),
      );
      return new Map(statsEntries);
    },
    [loadCopyTradeStatsForDay],
  );

  const handleCopyPayload = useCallback(
    async (
      action: JournalCopyAction,
      resolveEntries: () => Promise<JournalEntry[]>,
      successMessage: string,
      emptyMessage: string,
    ) => {
      if (!selectedAccountId) {
        showCopyToast("error", "Select an account before copying journal entries.");
        return;
      }

      setCopyAction(action);
      try {
        const sourceEntries = await resolveEntries();
        const entriesToCopy = sourceEntries.map((entry) => applyDraftToEntry(entry, selectedId, draft));
        if (entriesToCopy.length === 0) {
          showCopyToast("error", emptyMessage);
          return;
        }

        const tradeStatsByDate = await loadTradeStatsByDate(entriesToCopy);
        const text = buildJournalCopyText(entriesToCopy, tradeStatsByDate);
        const copied = await copyTextToClipboard(text);
        if (!copied) {
          throw new Error("Couldn't copy journal text. Check clipboard permissions and try again.");
        }

        showCopyToast("success", successMessage);
      } catch (error) {
        showCopyToast("error", getCopyFailureMessage(error));
      } finally {
        setCopyAction(null);
      }
    },
    [draft, loadTradeStatsByDate, selectedAccountId, selectedId, showCopyToast],
  );

  const handleCopyEntry = useCallback(() => {
    void handleCopyPayload(
      "entry",
      async () => {
        const entry = getCopyEntry(entries, selectedId);
        return entry ? [entry] : [];
      },
      "Copied current entry",
      "No journal entry is available to copy.",
    );
  }, [entries, handleCopyPayload, selectedId]);

  const handleCopyRecent = useCallback(
    (count: 7 | 30) => {
      void handleCopyPayload(
        count === 7 ? "recent-7" : "recent-30",
        async () => {
          if (!selectedAccountId) {
            return [];
          }

          const payload = await accountsApi.getJournalEntries(selectedAccountId, {
            ...listQuery,
            limit: count,
            offset: 0,
          });
          return payload.items;
        },
        count === 7 ? "Copied 7 recent entries" : "Copied 30 recent entries",
        "No journal entries are available to copy.",
      );
    },
    [handleCopyPayload, listQuery, selectedAccountId],
  );

  const savingDisabled = saveState === "saving" || !selectedEntry;
  const copyDisabled = loadingEntries || !selectedAccountId || totalEntries === 0;

  const moodOptions: Array<JournalMoodFilter> = ["ALL", "Focused", "Neutral", "Frustrated", "Confident"];
  const hasFilterStatusMessage = entriesError !== null || entriesInfo !== null;

  if (!selectedAccountId && !loadingEntries) {
    return (
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>Trading Journal</CardTitle>
          <CardDescription>Select an active account to start journaling.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="rounded-2xl border border-dashed border-slate-700/80 bg-slate-950/35 px-4 py-8 text-center">
            <p className="text-sm font-medium text-slate-200">No active account selected.</p>
            <p className="mt-2 text-sm text-slate-400">
              Choose an account from the header to create and review journal entries.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-5 pb-10">
      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="accent">Journal</Badge>
          {selectedAccount ? (
            <span className="rounded-full border border-slate-800/80 bg-slate-950/45 px-3 py-1 text-xs text-slate-300">
              {selectedAccount.name}
            </span>
          ) : null}
        </div>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-50">Trading Journal</h1>
          <p className="mt-2 max-w-3xl text-sm text-slate-400">
            Capture execution notes, emotional context, and trade snapshots in a clean review workflow for the
            active account.
          </p>
        </div>
      </section>

      <Card className="px-2 py-1.5 md:px-2.5 md:py-2">
        <CardHeader className="mb-0 py-0">
          <div className="flex flex-wrap items-center gap-1.5 lg:flex-nowrap">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5 lg:flex-nowrap">
              <FilterField label="Start" className="flex-[0_1_176px]">
                <Input
                  type="date"
                  value={startDate}
                  onChange={(event) => setStartDate(event.target.value)}
                  className="h-7 min-w-0 rounded-lg px-1.5 text-[11px]"
                />
              </FilterField>
              <FilterField label="End" className="flex-[0_1_176px]">
                <Input
                  type="date"
                  value={endDate}
                  onChange={(event) => setEndDate(event.target.value)}
                  className="h-7 min-w-0 rounded-lg px-1.5 text-[11px]"
                />
              </FilterField>
              <FilterField label="Mood" className="flex-[0_1_148px]">
                <Select
                  value={moodFilter}
                  onChange={(event) => setMoodFilter(event.target.value as JournalMoodFilter)}
                  className="h-7 min-w-0 rounded-lg px-1.5 text-[11px]"
                >
                  {moodOptions.map((option) => (
                    <option key={option} value={option}>
                      {option === "ALL" ? "All moods" : option}
                    </option>
                  ))}
                </Select>
              </FilterField>
            </div>
            <div className="ml-auto flex shrink-0 items-center gap-1.5">
              <JournalCopyActions
                disabled={copyDisabled}
                activeAction={copyAction}
                onCopyEntry={handleCopyEntry}
                onCopyRecent={handleCopyRecent}
              />
              <Button
                size="sm"
                className="h-7 shrink-0 rounded-lg px-2.5 text-[11px]"
                onClick={() => void handleCreateEntry()}
                disabled={creatingEntry || !selectedAccountId}
              >
                {creatingEntry ? "Creating..." : "New Entry"}
              </Button>
            </div>
          </div>
        </CardHeader>
        {hasFilterStatusMessage ? (
          <CardContent className="space-y-2 pt-2">
            {entriesError ? <InlineMessage tone="error">{entriesError}</InlineMessage> : null}
            {entriesInfo ? <InlineMessage tone="info">{entriesInfo}</InlineMessage> : null}
          </CardContent>
        ) : null}
      </Card>

      <div className="grid gap-5 xl:grid-cols-[minmax(320px,360px)_minmax(0,1fr)]">
        <div className="min-h-0 space-y-4">
          {loadingEntries ? (
            <JournalListSkeleton />
          ) : (
            <JournalList
              entries={entries}
              selectedId={selectedId}
              totalEntries={totalEntries}
              onSelect={(id) => void handleSelectEntry(id)}
            />
          )}
        </div>

        <div className="min-w-0">
          <JournalEditor
            entry={selectedEntry}
            draft={draft}
            saveState={saveState}
            savingDisabled={savingDisabled}
            conflictServerEntry={conflictServerEntry}
            images={images}
            imagesLoading={imagesLoading}
            imagesError={imagesError}
            uploadingImage={uploadingImage}
            deletingEntry={deletingEntry}
            onDraftChange={handleDraftChange}
            onArchiveToggle={() => void handleArchiveToggle()}
            onRetrySave={() => void handleRetrySave()}
            onReloadServerVersion={handleReloadServerVersion}
            onPasteImage={(file, selection) => void handlePasteImage(file, selection)}
            onDeleteImage={(imageId) => void handleDeleteImage(imageId)}
            onDeleteEntry={() => void handleDeleteEntry()}
          />
        </div>
      </div>
      {copyToast ? <CopyToast toast={copyToast} /> : null}
    </div>
  );
}
