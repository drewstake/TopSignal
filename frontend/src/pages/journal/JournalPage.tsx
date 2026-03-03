import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Toggle } from "../../components/ui/Toggle";
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
  JournalPullTradeStatsInput,
} from "../../lib/types";
import { DebouncedAutosaveQueue, type JournalSaveState } from "./journalAutosave";
import { JournalEditor } from "./components/JournalEditor";
import { JournalList } from "./components/JournalList";
import { getVersionConflictServerEntry } from "./journalConflict";
import {
  buildJournalQuery,
  entryToDraft,
  getTodayTradingDateIso,
  getYesterdayTradingDateIso,
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

  const [pullingStats, setPullingStats] = useState(false);
  const [pullStatsError, setPullStatsError] = useState<string | null>(null);
  const [conflictServerEntry, setConflictServerEntry] = useState<JournalEntry | null>(null);

  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [moodFilter, setMoodFilter] = useState<JournalMoodFilter>("ALL");
  const [queryText, setQueryText] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
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

  const handlePullTradeStats = useCallback(async () => {
    if (!selectedAccountId || !selectedEntry) {
      return;
    }

    setPullingStats(true);
    setPullStatsError(null);

    try {
      await flushAutosave();
      const payload: JournalPullTradeStatsInput = {};
      if (startDate) {
        payload.start_date = startDate;
      }
      if (endDate) {
        payload.end_date = endDate;
      }
      if (!payload.start_date && !payload.end_date) {
        payload.entry_date = selectedEntry.entry_date;
      }

      const updated = await accountsApi.pullJournalTradeStats(selectedAccountId, selectedEntry.id, payload);

      setEntries((currentEntries) => currentEntries.map((entry) => (entry.id === updated.id ? updated : entry)));
      selectedEntryVersionRef.current = updated.version;

      const nextDraft = entryToDraft(updated);
      setDraft(nextDraft);
      draftRef.current = nextDraft;
      autosaveRef.current?.setBaseline(toQueuedJournalSave(selectedAccountId, updated.id, nextDraft));
      setConflictServerEntry(null);
    } catch (err) {
      setPullStatsError(err instanceof Error ? err.message : "Failed to pull trade stats");
    } finally {
      setPullingStats(false);
    }
  }, [endDate, flushAutosave, selectedAccountId, selectedEntry, startDate]);

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

  const canGoPrev = currentPage > 1;
  const canGoNext = currentPage < totalPages;

  if (!selectedAccountId && !loadingEntries) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Journal</CardTitle>
          <CardDescription>Select an active account to start journaling.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <div className="space-y-6 pb-10">
      <Card>
        <CardHeader>
          <CardTitle>Trading Journal</CardTitle>
          <CardDescription>Capture structured notes for the active account and review patterns over time.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Start Date</label>
              <Input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">End Date</label>
              <Input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Mood</label>
              <Select value={moodFilter} onChange={(event) => setMoodFilter(event.target.value as JournalMoodFilter)}>
                {moodOptions.map((option) => (
                  <option key={option} value={option}>
                    {option === "ALL" ? "All moods" : option}
                  </option>
                ))}
              </Select>
            </div>
            <div className="xl:col-span-2">
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Search</label>
              <Input
                value={queryText}
                onChange={(event) => setQueryText(event.target.value)}
                placeholder="Search title, body, or tags"
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <Toggle checked={includeArchived} onChange={setIncludeArchived} label="Include archived entries" />
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                onClick={() => void handleCreateEntry(getYesterdayTradingDateIso())}
                disabled={creatingEntry || !selectedAccountId}
              >
                Yesterday
              </Button>
              <Button onClick={() => void handleCreateEntry()} disabled={creatingEntry || !selectedAccountId}>
                {creatingEntry ? "Creating..." : "New Entry"}
              </Button>
            </div>
          </div>

          {entriesError ? <p className="text-sm text-rose-300">{entriesError}</p> : null}
          {entriesInfo ? <p className="text-sm text-amber-200">{entriesInfo}</p> : null}
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-3 xl:col-span-1">
          {loadingEntries ? (
            <Card>
              <CardContent className="py-6 text-sm text-slate-400">Loading journal entries...</CardContent>
            </Card>
          ) : (
            <JournalList entries={entries} selectedId={selectedId} onSelect={(id) => void handleSelectEntry(id)} />
          )}
          <Card>
            <CardContent className="flex items-center justify-between py-3 text-xs text-slate-400">
              <p>
                Page {currentPage} of {totalPages} ({totalEntries} total)
              </p>
              <div className="flex items-center gap-2">
                <Button variant="ghost" size="sm" onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={!canGoPrev}>
                  Prev
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
                  disabled={!canGoNext}
                >
                  Next
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>

        <div className="xl:col-span-2">
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
            pullingStats={pullingStats}
            pullStatsError={pullStatsError}
            deletingEntry={deletingEntry}
            onDraftChange={handleDraftChange}
            onArchiveToggle={() => void handleArchiveToggle()}
            onRetrySave={() => void handleRetrySave()}
            onReloadServerVersion={handleReloadServerVersion}
            onUploadImage={(file) => void handleUploadImage(file)}
            onDeleteImage={(imageId) => void handleDeleteImage(imageId)}
            onPullTradeStats={() => void handlePullTradeStats()}
            onDeleteEntry={() => void handleDeleteEntry()}
          />
        </div>
      </div>
    </div>
  );
}
