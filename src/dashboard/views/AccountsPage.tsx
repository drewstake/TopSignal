import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { hasSessionToken } from "../../lib/session";
import { searchAccounts } from "../../api/account";
import type { TopstepAccount } from "../../api/account";
import { fmtMoney } from "../../lib/format";
import { getActiveAccountId, setActiveAccountId } from "../../lib/activeAccount";
import { detectAccountTypeFromName, type AccountType } from "../../lib/accountType";

export default function AccountsPage() {
  const [onlyActive, setOnlyActive] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<TopstepAccount[]>([]);
  const [activeId, setActiveId] = useState<number | null>(() => getActiveAccountId());

  const connected = hasSessionToken();

  const totals = useMemo(() => {
    const count = accounts.length;
    const canTradeCount = accounts.filter((a) => a.canTrade).length;
    const visibleCount = accounts.filter((a) => a.isVisible).length;
    const totalBalance = accounts.reduce((sum, a) => sum + (Number(a.balance) || 0), 0);

    const byType: Record<AccountType, number> = {
      XFA: 0,
      Combine: 0,
      Practice: 0,
      Unknown: 0,
    };

    for (const a of accounts) {
      const t = detectAccountTypeFromName(a.name);
      byType[t] += 1;
    }

    return { count, canTradeCount, visibleCount, totalBalance, byType };
  }, [accounts]);

  async function load() {
    setError(null);

    if (!connected) {
      setAccounts([]);
      setError("Connect in Settings first.");
      return;
    }

    setLoading(true);
    try {
      const res = await searchAccounts({
        onlyActiveAccounts: onlyActive,
        includeInvisibleAccounts: !onlyActive,
      });

      if (!res.success || res.errorCode !== 0) {
        throw new Error(res.errorMessage || `Request failed (errorCode ${res.errorCode}).`);
      }

      setAccounts(res.accounts || []);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to load accounts.";
      setAccounts([]);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onlyActive]);

  function onSetActive(a: TopstepAccount) {
    setActiveAccountId(a.id);
    setActiveId(a.id);
  }

  return (
    <div className="grid grid-cols-1 gap-3">
      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-lg font-semibold text-zinc-100">Accounts</div>
            <div className="mt-1 text-sm text-zinc-400">Lists your accounts after you connect.</div>
          </div>

          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 text-sm text-zinc-300">
              <input
                type="checkbox"
                checked={onlyActive}
                onChange={(e) => setOnlyActive(e.target.checked)}
                className="h-4 w-4 accent-zinc-200"
              />
              Only active
            </label>

            <button
              onClick={load}
              className="rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-sm text-zinc-200"
            >
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

        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-6">
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Accounts</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{totals.count}</div>
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Combines</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{totals.byType.Combine}</div>
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">XFAs</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{totals.byType.XFA}</div>
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Practice</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{totals.byType.Practice}</div>
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Can trade</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{totals.canTradeCount}</div>
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Total balance</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtMoney(totals.totalBalance)}</div>
          </div>
        </div>

        {error ? (
          <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-sm text-zinc-200">
            {error}
          </div>
        ) : null}

        <div className="mt-4 overflow-hidden rounded-2xl border border-zinc-800">
          <div className="grid grid-cols-12 bg-zinc-950/40 px-4 py-2 text-xs text-zinc-400">
            <div className="col-span-4">Name</div>
            <div className="col-span-2">Balance</div>
            <div className="col-span-2">Type</div>
            <div className="col-span-1">Trade</div>
            <div className="col-span-1">Visible</div>
            <div className="col-span-2 text-right">Actions</div>
          </div>

          <div className="divide-y divide-zinc-800 bg-zinc-950/20">
            {loading ? (
              <div className="px-4 py-4 text-sm text-zinc-300">Loading...</div>
            ) : accounts.length === 0 ? (
              <div className="px-4 py-4 text-sm text-zinc-300">No accounts found.</div>
            ) : (
              accounts.map((a, index) => {
                const t = detectAccountTypeFromName(a.name);

                return (
                  <div
                    key={a.id}
                    className={
                      "grid grid-cols-12 items-center px-4 py-3 text-sm text-zinc-200 " +
                      (index % 2 === 0 ? "bg-zinc-950/10" : "bg-transparent")
                    }
                  >
                    <div className="col-span-4">
                      <div className="flex items-center gap-2">
                        <div className="font-medium text-zinc-100">{a.name}</div>
                        {activeId === a.id ? (
                          <div className="rounded-full border border-emerald-700 bg-emerald-950/40 px-2 py-0.5 text-xs text-emerald-200">
                            Active
                          </div>
                        ) : null}
                      </div>
                      <div className="mt-0.5 text-xs text-zinc-400">ID: {a.id}</div>
                    </div>

                    <div className="col-span-2">{fmtMoney(a.balance)}</div>

                    <div className="col-span-2">
                      <div className="text-zinc-200">{t}</div>
                    </div>

                    <div className="col-span-1">{a.canTrade ? "Yes" : "No"}</div>
                    <div className="col-span-1">{a.isVisible ? "Yes" : "No"}</div>

                    <div className="col-span-2 flex justify-end">
                      <button
                        onClick={() => onSetActive(a)}
                        className="rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-xs text-zinc-200"
                      >
                        Set active
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-xs text-zinc-400">
          Using: POST <span className="text-zinc-200">/api/Account/search</span>
        </div>
      </div>
    </div>
  );
}
