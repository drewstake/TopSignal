import { useState, type ReactNode } from "react";

import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import type { AccountInfo } from "../../lib/types";
import { isProjectXBotAccount } from "./botAccountIsolation";
import { BotMarketPanels } from "./BotMarketPanels";
import { OrderBookPanel } from "./OrderBookPanel";

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

export function BotProjectXAccountNotice({
  activeAccount,
  projectXAccounts,
  onSelectAccount,
}: {
  activeAccount: AccountInfo | null;
  projectXAccounts: AccountInfo[];
  onSelectAccount: (accountId: number) => void;
}) {
  return (
      <section
        className="rounded-xl border border-cyan-400/25 bg-cyan-500/5 p-5"
        aria-labelledby="bot-projectx-account-notice-title"
      >
        <h2 id="bot-projectx-account-notice-title" className="text-lg font-semibold text-slate-100">
          Explore Bot without an account
        </h2>
        <p className="mt-2 max-w-2xl text-sm text-slate-300">
          You can view the chart, order book, and market analysis without a trading account. Market data uses your ProjectX connection.
          {activeAccount ? ` Your ${activeAccount.name} account stays local.` : " No account is selected."}
          {" "}Select a ProjectX account to enable bot controls. Execution is disabled in this view.
        </p>
        {projectXAccounts.length > 0 ? (
          <label className="mt-4 block max-w-md space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
            <span>ProjectX account</span>
            <select
              className="h-10 w-full rounded-xl border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/55"
              value=""
              onChange={(event) => {
                const accountId = Number.parseInt(event.target.value, 10);
                if (Number.isFinite(accountId)) {
                  onSelectAccount(accountId);
                }
              }}
              aria-label="Select a ProjectX account for Bot"
            >
              <option value="">Choose a ProjectX account</option>
              {projectXAccounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name} ({account.id})
                </option>
              ))}
            </select>
          </label>
        ) : (
          <p className="mt-4 text-sm text-slate-300">
            No saved ProjectX account is available. <a className="text-cyan-300 underline underline-offset-2" href="/accounts">Open Accounts</a>{" "}
            to connect one when you are ready.
          </p>
        )}
      </section>
  );
}

export function BotMarketAccountPreview({ activeAccount, demoMode }: { activeAccount: AccountInfo | null; demoMode: boolean }) {
  const [connectMarketData, setConnectMarketData] = useState(false);
  const canConnect = activeAccount === null || connectMarketData;
  if (canConnect) {
    return <BotMarketPanels bot={null} authenticatedCacheScope={null} activity={null} evaluation={null}
      refreshToken={0} demoMode={demoMode} evaluating={false} />;
  }
  return (
    <div className="order-1 min-w-0 space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>Signal Chart</CardTitle>
          <CardDescription>MNQ · Candles and strategy signals</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="mb-3 text-sm text-app-muted">Your CSV account stays local. Load ProjectX market data to view the chart, order book, and analysis without enabling bot execution.</p>
          <Button disabled={demoMode} onClick={() => setConnectMarketData(true)}>Load ProjectX chart</Button>
        </CardContent>
      </Card>
      <OrderBookPanel contractId={null} demoMode={demoMode} />
      <Card>
        <CardHeader><CardTitle>Evaluation &amp; market analysis</CardTitle></CardHeader>
        <CardContent className="text-sm text-app-muted">
          Load ProjectX market data to analyze the chart’s completed candles. No trading account is required.
        </CardContent>
      </Card>
    </div>
  );
}
