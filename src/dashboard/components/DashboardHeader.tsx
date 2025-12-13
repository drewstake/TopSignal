import { Link } from "react-router-dom";

interface DashboardHeaderProps {
  mode: "active" | "all";
  setMode: (mode: "active" | "all") => void;
  daysBack: number;
  setDaysBack: (days: number) => void;
  onRefresh: () => void;
  connected: boolean;
  activeAccountId: number | null;
  onlyActiveAccounts: boolean;
  setOnlyActiveAccounts: (value: boolean) => void;
  includeInvisibleAccounts: boolean;
  setIncludeInvisibleAccounts: (value: boolean) => void;
  error: string | null;
}

export default function DashboardHeader({
  mode,
  setMode,
  daysBack,
  setDaysBack,
  onRefresh,
  connected,
  activeAccountId,
  onlyActiveAccounts,
  setOnlyActiveAccounts,
  includeInvisibleAccounts,
  setIncludeInvisibleAccounts,
  error,
}: DashboardHeaderProps) {
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-lg font-semibold text-zinc-100">Dashboard</div>
          <div className="mt-1 text-sm text-zinc-400">
            Note: this page is accurate for PnL, fees, win rate, day stats, and drawdown from daily equity.
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1 rounded-xl border border-zinc-800 bg-zinc-950/40 p-1 text-sm">
            <button
              onClick={() => setMode("active")}
              className={
                "rounded-lg px-3 py-1.5 " + (mode === "active" ? "bg-zinc-800 text-zinc-100" : "text-zinc-300")
              }
            >
              Active account
            </button>
            <button
              onClick={() => setMode("all")}
              className={"rounded-lg px-3 py-1.5 " + (mode === "all" ? "bg-zinc-800 text-zinc-100" : "text-zinc-300")}
            >
              All accounts
            </button>
          </div>

          <div className="flex items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-sm text-zinc-200">
            <div className="text-zinc-400">Days</div>
            <input
              type="number"
              min={1}
              max={365}
              value={daysBack}
              onChange={(e) => setDaysBack(Number(e.target.value))}
              className="w-20 rounded-lg border border-zinc-800 bg-zinc-950/40 px-2 py-1 text-zinc-100"
            />
          </div>

          <button onClick={onRefresh} className="rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-sm text-zinc-200">
            Refresh
          </button>
        </div>
      </div>

      {!connected ? (
        <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-sm text-zinc-200">
          You are not connected. Go to{" "}
          <Link to="/settings" className="underline">
            Settings
          </Link>{" "}
          and connect first.
        </div>
      ) : null}

      {mode === "active" ? (
        <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-sm text-zinc-200">
          Active account ID: <span className="text-zinc-100">{activeAccountId ?? "None"}</span>. Set it on{" "}
          <Link to="/accounts" className="underline">
            Accounts
          </Link>
          .
        </div>
      ) : (
        <div className="mt-4 flex flex-wrap items-center gap-4 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-sm text-zinc-200">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={onlyActiveAccounts}
              onChange={(e) => setOnlyActiveAccounts(e.target.checked)}
              className="h-4 w-4 accent-zinc-200"
            />
            Only active accounts
          </label>

          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={includeInvisibleAccounts}
              onChange={(e) => setIncludeInvisibleAccounts(e.target.checked)}
              className="h-4 w-4 accent-zinc-200"
            />
            Include invisible accounts (needed for most inactive accounts)
          </label>
        </div>
      )}

      {error ? (
        <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-sm text-zinc-200">{error}</div>
      ) : null}
    </div>
  );
}
