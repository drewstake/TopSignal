import type { ReactNode } from "react";

import type { AccountInfo } from "../../lib/types";
import { isProjectXBotAccount } from "./botAccountIsolation";

export function BotProviderWorkspaceBoundary({
  activeAccount,
  fallback,
  children,
}: {
  activeAccount: AccountInfo | null;
  fallback: ReactNode;
  children: ReactNode;
}) {
  return isProjectXBotAccount(activeAccount) ? children : fallback;
}

export function BotExpressAccountRequired({
  activeAccount,
  expressAccounts,
  onSelectAccount,
}: {
  activeAccount: AccountInfo | null;
  expressAccounts: AccountInfo[];
  onSelectAccount: (accountId: number) => void;
}) {
  return (
    <div className="space-y-5 pb-8">
      <h1 className="sr-only">Trading Bot</h1>
      <section
        className="rounded-xl border border-cyan-400/25 bg-cyan-500/5 p-5"
        aria-labelledby="bot-express-account-required-title"
      >
        <h2 id="bot-express-account-required-title" className="text-lg font-semibold text-slate-100">
          Select an Express account to use Bot
        </h2>
        <p className="mt-2 max-w-2xl text-sm text-slate-300">
          Bot uses ProjectX market data. Your {activeAccount?.name ?? "Live CSV account"} stays local, so no
          ProjectX charts, searches, polling, or streams start while it is active.
        </p>
        {expressAccounts.length > 0 ? (
          <label className="mt-4 block max-w-md space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
            <span>Express account</span>
            <select
              className="h-10 w-full rounded-xl border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/55"
              value=""
              onChange={(event) => {
                const accountId = Number.parseInt(event.target.value, 10);
                if (Number.isFinite(accountId)) {
                  onSelectAccount(accountId);
                }
              }}
              aria-label="Select an Express account for Bot"
            >
              <option value="">Choose an Express account</option>
              {expressAccounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name} ({account.id})
                </option>
              ))}
            </select>
          </label>
        ) : (
          <p className="mt-4 text-sm text-slate-300">
            No saved Express account is available. <a className="text-cyan-300 underline underline-offset-2" href="/accounts">Open Accounts</a>{" "}
            to connect one.
          </p>
        )}
      </section>
    </div>
  );
}
