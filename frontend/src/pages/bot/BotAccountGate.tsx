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
        className="flex flex-col gap-4 rounded-xl border border-app-border bg-app-surface px-5 py-4 sm:flex-row sm:items-center sm:justify-between"
        aria-labelledby="bot-projectx-account-notice-title"
      >
        <div className="min-w-0 max-w-3xl">
          <h2 id="bot-projectx-account-notice-title" className="text-sm font-semibold text-app-text">
            Explore Bot without an account
          </h2>
          <p className="mt-1 text-xs leading-5 text-app-muted">
            View charts, order book, and analysis through your ProjectX connection.
            {activeAccount ? ` Your ${activeAccount.name} account stays local.` : " No account is selected."}
            {" "}Select a ProjectX account to enable bot controls. Execution is disabled in this view.
          </p>
        </div>
        {projectXAccounts.length > 0 ? (
          <label className="block w-full shrink-0 space-y-1.5 text-xs font-medium text-app-muted sm:w-64">
            <span>ProjectX account</span>
            <select
              className="h-10 w-full rounded-lg border border-app-border bg-app-bg px-3 text-sm text-app-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-accent/55"
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
          <div className="flex shrink-0 flex-wrap items-center gap-3 sm:justify-end">
            <span className="text-xs text-app-muted">No ProjectX account connected</span>
            <a
              className="inline-flex h-9 items-center justify-center whitespace-nowrap rounded-lg border border-app-border px-3 text-xs font-medium text-app-text transition-colors hover:bg-app-bg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-accent/55"
              href="/accounts"
            >Open Accounts</a>
          </div>
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
