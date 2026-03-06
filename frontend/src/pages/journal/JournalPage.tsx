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
import type {
  AccountInfo,
  JournalEntry,
  JournalEntryImage,
  JournalEntryUpdateInput,
} from "../../lib/types";
import { DebouncedAutosaveQueue, type JournalSaveState } from "./journalAutosave";
import { JournalEditor } from "./components/JournalEditor";
import { JournalList } from "./components/JournalList";
import { getVersionConflictServerEntry } from "./journalConflict";
import {
  buildJournalQuery,
  entryToDraft,
  getTodayTradingDateIso,
  JOURNAL_AUTOSAVE_DELAY_MS,
  JOURNAL_PAGE_SIZE,
  parseTagsInput,
  reconcileDraftWithServerEntry,
  type JournalDraft,
  type JournalMoodFilter,
} from "./journalUtils";

const JOURNAL_DATE_QUERY_PARAM = "date";

type JournalAutosavePatch = Omit<JournalEntryUpdateInput, "version">;

interface QueuedJournalSave {
  accountId: number;
  entryId: number;
  patch: JournalAutosavePatch;
}

function parseJournalDateParam(value: string | null): string | null {
  if (!value) {
    return null;
  }
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
}

function draftToAutosavePatch(draft: JournalDraft): JournalAutosavePatch {
  return {
    title: draft.title,
    mood: draft.mood,
    tags: parseTagsInput(draft.tagsInput),
    body: draft.body,
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

function FilterField({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-[11px] uppercase tracking-[0.14em] text-slate-500">{label}</span>
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
    <Card>
      <CardHeader>
        <CardTitle>Journal Entries</CardTitle>
        <CardDescription>Loading your recent entries and filters.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
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

  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [moodFilter, setMoodFilter] = useState<JournalMoodFilter>("ALL");
  const [queryText, setQueryText] = useState("");
  const [includeArchived] = useState(false);
  const [page, setPage] = useState(1);

  const autosaveRef = useRef<DebouncedAutosaveQueue<QueuedJournalSave> | null>(null);
  const selectedEntryIdRef = useRef<number | null>(null);
  const selectedAccountIdRef = useRef<number | null>(null);
  const selectedEntryVersionRef = useRef<number | null>(null);
  const draftRef = useRef<JournalDraft | null>(null);
  const draftEntryIdRef = useRef<number | null>(null);
  const includeArchivedRef = useRef(includeArchived);
  const handledDateKeyRef = useRef<string | null>(null);
  const entriesRequestVersionRef = useRef(0);

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
        queryText,
        includeArchived,
        limit: JOURNAL_PAGE_SIZE,
        offset,
      }),
    [endDate, includeArchived, moodFilter, offset, queryText, startDate],
  );

  useEffect(() => {
    setPage(1);
  }, [startDate, endDate, moodFilter, queryText, includeArchived]);

  const selectedEntry = useMemo(
    () => entries.find((entry) => entry.id === selectedId) ?? null,
    [entries, selectedId],
  );
  selectedEntryIdRef.current = selectedEntry?.id ?? null;

  const flushAutosave = useCallback(async () => {
    if (!autosaveRef.current) {
      return;
    }
    await autosaveRef.current.flush();
  }, []);

  const loadEntries = useCallback(async () => {
    const requestVersion = entriesRequestVersionRef.current + 1;
    entriesRequestVersionRef.current = requestVersion;
    if (!selectedAccountId) {
      setEntries([]);
      setTotalEntries(0);
      setEntriesError(null);
      setSelectedId(null);
      return;
    }

    setLoadingEntries(true);
    setEntriesError(null);

    try {
      await flushAutosave();
      const payload = await accountsApi.getJournalEntries(selectedAccountId, listQuery);
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
    void loadEntries();
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
          const hasEntry = currentEntries.some((entry) => entry.id === updated.id);
          if (!hasEntry) {
            return currentEntries;
          }
          if (updated.is_archived && !includeArchivedRef.current) {
            return currentEntries.filter((entry) => entry.id !== updated.id);
          }
          return currentEntries.map((entry) => (entry.id === updated.id ? updated : entry));
        });
        setTotalEntries((currentTotal) =>
          updated.is_archived && !includeArchivedRef.current ? Math.max(0, currentTotal - 1) : currentTotal,
        );

        selectedEntryVersionRef.current = updated.version;

        const currentDraft = draftRef.current;
        if (!currentDraft) {
          return;
        }

        const currentPatch = draftToAutosavePatch(currentDraft);
        if (journalAutosavePatchEquals(currentPatch, payload.patch)) {
          const normalizedDraft = entryToDraft(updated);
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
    return () => {
      void flushAutosave();
    };
  }, [flushAutosave, selectedAccountId]);

  const loadEntryImages = useCallback(async () => {
    if (!selectedAccountId || !selectedEntry?.id) {
      setImages([]);
      setImagesError(null);
      return;
    }

    setImagesLoading(true);
    setImagesError(null);

    try {
      const rows = await accountsApi.listJournalImages(selectedAccountId, selectedEntry.id);
      setImages(rows);
    } catch (err) {
      setImages([]);
      setImagesError(err instanceof Error ? err.message : "Failed to load images");
    } finally {
      setImagesLoading(false);
    }
  }, [selectedAccountId, selectedEntry?.id]);

  useEffect(() => {
    void loadEntryImages();
  }, [loadEntryImages]);

  const handleDraftChange = useCallback((nextDraft: JournalDraft) => {
    setDraft(nextDraft);
    draftRef.current = nextDraft;

    const accountId = selectedAccountIdRef.current;
    const entryId = selectedEntryIdRef.current;
    if (!accountId || !entryId) {
      return;
    }

    autosaveRef.current?.queue(toQueuedJournalSave(accountId, entryId, nextDraft));
  }, []);

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

  const handleUploadImage = useCallback(
    async (file: File | Blob) => {
      if (!selectedAccountId || !selectedEntry) {
        return;
      }

      setUploadingImage(true);
      setImagesError(null);
      try {
        const uploaded = await accountsApi.uploadJournalImage(selectedAccountId, selectedEntry.id, file);
        setImages((current) => [...current, uploaded]);
      } catch (err) {
        setImagesError(err instanceof Error ? err.message : "Failed to upload image");
      } finally {
        setUploadingImage(false);
      }
    },
    [selectedAccountId, selectedEntry],
  );

  const handleDeleteImage = useCallback(
    async (imageId: number) => {
      if (!selectedAccountId || !selectedEntry) {
        return;
      }

      try {
        await accountsApi.deleteJournalImage(selectedAccountId, selectedEntry.id, imageId);
        setImages((current) => current.filter((image) => image.id !== imageId));
      } catch (err) {
        setImagesError(err instanceof Error ? err.message : "Failed to delete image");
      }
    },
    [selectedAccountId, selectedEntry],
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

  const savingDisabled = saveState === "saving" || !selectedEntry;

  const moodOptions: Array<JournalMoodFilter> = ["ALL", "Focused", "Neutral", "Frustrated", "Confident"];

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

      <Card>
        <CardHeader className="mb-3 flex items-center justify-between gap-3 space-y-0">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <CardTitle>Filters and Quick Actions</CardTitle>
              <CardDescription className="text-[11px] leading-5">
                Refine entries and create new ones.
              </CardDescription>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button size="sm" onClick={() => void handleCreateEntry()} disabled={creatingEntry || !selectedAccountId}>
              {creatingEntry ? "Creating..." : "New Today"}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[150px_150px_160px_minmax(220px,1fr)]">
            <FilterField label="Start Date">
              <Input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
            </FilterField>
            <FilterField label="End Date">
              <Input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
            </FilterField>
            <FilterField label="Mood">
              <Select value={moodFilter} onChange={(event) => setMoodFilter(event.target.value as JournalMoodFilter)}>
                {moodOptions.map((option) => (
                  <option key={option} value={option}>
                    {option === "ALL" ? "All moods" : option}
                  </option>
                ))}
              </Select>
            </FilterField>
            <FilterField label="Search">
              <Input
                value={queryText}
                onChange={(event) => setQueryText(event.target.value)}
                placeholder="Search notes, titles, or tags"
              />
            </FilterField>
          </div>

          {entriesError ? <InlineMessage tone="error">{entriesError}</InlineMessage> : null}
          {entriesInfo ? <InlineMessage tone="info">{entriesInfo}</InlineMessage> : null}
        </CardContent>
      </Card>

      <div className="grid gap-5 xl:grid-cols-[minmax(320px,360px)_minmax(0,1fr)]">
        <div className="space-y-4 xl:sticky xl:top-24 xl:self-start">
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
            onUploadImage={(file) => void handleUploadImage(file)}
            onDeleteImage={(imageId) => void handleDeleteImage(imageId)}
            onDeleteEntry={() => void handleDeleteEntry()}
          />
        </div>
      </div>
    </div>
  );
}
