import { Outlet, useLocation } from "react-router-dom";

import { Tabs } from "../components/ui/Tabs";
import { ACCOUNT_QUERY_PARAM, parseAccountId, readStoredAccountId } from "../lib/accountSelection";

export function AppShell() {
  const location = useLocation();
  const queryAccountId = parseAccountId(new URLSearchParams(location.search).get(ACCOUNT_QUERY_PARAM));
  const activeAccountId = queryAccountId ?? readStoredAccountId();

  const accountSuffix = activeAccountId ? `?${ACCOUNT_QUERY_PARAM}=${activeAccountId}` : "";

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="sticky top-0 z-30 border-b border-slate-800/80 bg-slate-950/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-4 px-4 py-4 lg:px-8">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-lg font-semibold tracking-tight text-slate-100">TopSignal</p>
              <p className="text-xs text-slate-400">ProjectX Account + Trade Dashboard</p>
            </div>
            <div className="rounded-full border border-slate-700 bg-slate-900/80 px-3 py-1 text-xs font-medium text-slate-300">
              {activeAccountId ? `Active Account ${activeAccountId}` : "No account selected"}
            </div>
          </div>

          <Tabs
            items={[
              { label: "Dashboard", to: "/" },
              { label: "Accounts", to: `/accounts${accountSuffix}` },
              { label: "Trades", to: `/trades${accountSuffix}` },
            ]}
          />
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1400px] px-4 py-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  );
}
