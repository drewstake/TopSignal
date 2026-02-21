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
  writeStoredAccountId,
} from "../../lib/accountSelection";
import { accountsApi } from "../../lib/api";
import type { AccountInfo, JournalEntry } from "../../lib/types";
import { DebouncedAutosaveQueue, type JournalSaveState } from "./journalAutosave";
import { JournalEditor } from "./components/JournalEditor";
import { JournalList } from "./components/JournalList";
import {
  buildJournalQuery,
  draftToUpdatePayload,
  entryToDraft,
  getTodayUtcDateIso,
  journalPayloadEquals,
  JOURNAL_AUTOSAVE_DELAY_MS,
  JOURNAL_PAGE_SIZE,
  type JournalDraft,
  type JournalMoodFilter,
} from "./journalUtils";

interface QueuedJournalSave {
  accountId: number;
  entryId: number;
  patch: ReturnType<typeof draftToUpdatePayload>;
}

function toQueuedJournalSave(accountId: number, entryId: number, draft: JournalDraft): QueuedJournalSave {
  return {
    accountId,
    entryId,
    patch: draftToUpdatePayload(draft),
  };
}

function queuedJournalSaveEquals(left: QueuedJournalSave, right: QueuedJournalSave): boolean {
  return (
    left.accountId === right.accountId &&
    left.entryId === right.entryId &&
    journalPayloadEquals(left.patch, right.patch)
  );
}

export function JournalPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [totalEntries, setTotalEntries] = useState(0);
  const [loadingEntries, setLoadingEntries] = useState(false);
  const [entriesError, setEntriesError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draft, setDraft] = useState<JournalDraft | null>(null);
  const [saveState, setSaveState] = useState<JournalSaveState>("saved");
  const [creatingEntry, setCreatingEntry] = useState(false);

  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [moodFilter, setMoodFilter] = useState<JournalMoodFilter>("ALL");
  const [queryText, setQueryText] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [page, setPage] = useState(1);

  const autosaveRef = useRef<DebouncedAutosaveQueue<QueuedJournalSave> | null>(null);
  const selectedEntryIdRef = useRef<number | null>(null);
  const selectedAccountIdRef = useRef<number | null>(null);
  const draftRef = useRef<JournalDraft | null>(null);
  const includeArchivedRef = useRef(includeArchived);

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
      const payload = await accountsApi.getAccounts();
      setAccounts(payload);
    } catch {
      setAccounts([]);
    }
  }, []);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    if (accounts.length === 0) {
      return;
    }

    if (accountFromQuery && accounts.some((account) => account.id === accountFromQuery)) {
      writeStoredAccountId(accountFromQuery);
      return;
    }

    const storedAccountId = readStoredAccountId();
    if (storedAccountId && accounts.some((account) => account.id === storedAccountId)) {
      setActiveAccount(storedAccountId);
      return;
    }

    setActiveAccount(accounts[0].id);
  }, [accounts, accountFromQuery, setActiveAccount]);

  const selectedAccount = useMemo(
    () => accounts.find((account) => account.id === accountFromQuery) ?? null,
    [accounts, accountFromQuery],
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
      setEntries(payload.items);
      setTotalEntries(payload.total);
      setSelectedId((currentSelectedId) => {
        if (currentSelectedId && payload.items.some((entry) => entry.id === currentSelectedId)) {
          return currentSelectedId;
        }
        return payload.items[0]?.id ?? null;
      });
    } catch (err) {
      setEntries([]);
      setTotalEntries(0);
      setSelectedId(null);
      setEntriesError(err instanceof Error ? err.message : "Failed to load journal entries");
    } finally {
      setLoadingEntries(false);
    }
  }, [flushAutosave, listQuery, selectedAccountId]);

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
    const queue = new DebouncedAutosaveQueue({
      delayMs: JOURNAL_AUTOSAVE_DELAY_MS,
      equals: queuedJournalSaveEquals,
      onStateChange: setSaveState,
      save: async (payload) => {
        const updated = await accountsApi.updateJournalEntry(payload.accountId, payload.entryId, payload.patch);
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
        const currentDraft = draftRef.current;
        if (!currentDraft) {
          return;
        }
        const currentPayload = draftToUpdatePayload(currentDraft);
        if (!journalPayloadEquals(currentPayload, payload.patch)) {
          return;
        }
        const normalizedDraft = entryToDraft(updated);
        setDraft(normalizedDraft);
        draftRef.current = normalizedDraft;
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
      setSaveState("saved");
      return;
    }

    const nextDraft = entryToDraft(selectedEntry);
    setDraft(nextDraft);
    draftRef.current = nextDraft;
    if (!selectedAccountId) {
      return;
    }
    autosaveRef.current?.setBaseline(toQueuedJournalSave(selectedAccountId, selectedEntry.id, nextDraft));
  }, [selectedAccountId, selectedEntry?.id]);

  useEffect(() => {
    return () => {
      void flushAutosave();
    };
  }, [flushAutosave, selectedAccountId]);

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

  const handleCreateEntry = useCallback(async () => {
    if (!selectedAccountId) {
      return;
    }

    setCreatingEntry(true);
    setEntriesError(null);
    try {
      await flushAutosave();
      const created = await accountsApi.createJournalEntry(selectedAccountId, {
        entry_date: getTodayUtcDateIso(),
        title: "New Entry",
        mood: "Neutral",
        tags: [],
        body: "",
      });
      setEntries((currentEntries) => [created, ...currentEntries]);
      setTotalEntries((currentTotal) => currentTotal + 1);
      setPage(1);
      setSelectedId(created.id);
    } catch (err) {
      setEntriesError(err instanceof Error ? err.message : "Failed to create journal entry");
    } finally {
      setCreatingEntry(false);
    }
  }, [flushAutosave, selectedAccountId]);

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
              <Button onClick={() => void handleCreateEntry()} disabled={creatingEntry || !selectedAccountId}>
                {creatingEntry ? "Creating..." : "New Entry"}
              </Button>
            </div>
          </div>

          {entriesError ? <p className="text-sm text-rose-300">{entriesError}</p> : null}
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
            onDraftChange={handleDraftChange}
            onArchiveToggle={() => void handleArchiveToggle()}
            onRetrySave={() => void handleRetrySave()}
          />
        </div>
      </div>
    </div>
  );
}
