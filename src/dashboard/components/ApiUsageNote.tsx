interface ApiUsageNoteProps {
  mode: "active" | "all";
}

export default function ApiUsageNote({ mode }: ApiUsageNoteProps) {
  return (
    <div className="mt-4 rounded-xl border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/30 px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400">
      {mode === "active" ? (
        <>
          Using: POST <span className="text-zinc-800 dark:text-zinc-200">/api/Trade/search</span> (active account only)
        </>
      ) : (
        <>
          Using: POST <span className="text-zinc-800 dark:text-zinc-200">/api/Account/search</span> + POST <span className="text-zinc-800 dark:text-zinc-200">/api/Trade/search</span>
          (all accounts aggregation)
        </>
      )}
    </div>
  );
}
