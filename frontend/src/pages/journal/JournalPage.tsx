import { useMemo, useState } from "react";
import { journalEntries } from "../../mock/data";
import { JournalEditor } from "./components/JournalEditor";
import { JournalList } from "./components/JournalList";

export function JournalPage() {
  const [selectedId, setSelectedId] = useState(journalEntries[0]?.id ?? "");

  const selectedEntry = useMemo(
    () => journalEntries.find((entry) => entry.id === selectedId) ?? journalEntries[0],
    [selectedId],
  );

  if (!selectedEntry) {
    return null;
  }

  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <div className="xl:col-span-1">
        <JournalList entries={journalEntries} selectedId={selectedEntry.id} onSelect={setSelectedId} />
      </div>
      <div className="xl:col-span-2">
        <JournalEditor entry={selectedEntry} />
      </div>
    </div>
  );
}
