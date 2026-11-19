import { useOutletContext } from "react-router-dom";
import type { AppShellOutletContext } from "../../app/AppShell";
import { useDemoMode } from "../../lib/demoMode";
import { getBotProviderAccountId } from "../bot/botAccountIsolation";
import { useImbalanceReport } from "../bot/imbalanceReport";
import { TopBotComparison } from "../bot/TopBotComparison";
import { SignalPanel } from "../bot/TopBotEdgePanel";
import { TopBotResearchStatus } from "../bot/TopBotResearchStatus";
import { TopBotTradeRecorder } from "../bot/TopBotTradeRecorder";
import { RecordedSignalReplay } from "./RecordedSignalReplay";
import "../bot/TopBotDashboard.css";

export function RecordersPage() {
  const { accounts, accountsLoading, accountsError, selectedAccountId } = useOutletContext<AppShellOutletContext>();
  const { enabled: demo } = useDemoMode();
  const accountId = getBotProviderAccountId(accounts.find(account => account.id === selectedAccountId) ?? null);
  const message = demo ? "Live recordings are available outside Demo Mode."
    : accountsLoading ? "Loading accounts…" : accountsError ? "Accounts could not be loaded."
    : accountId === null ? "Select a connected ProjectX account to view its recordings." : null;
  return <div className="topbot-dashboard">
    <h1 className="text-2xl font-semibold">Recorders</h1>
    {accountId !== null && !message ? <AccountRecordings key={accountId} accountId={accountId} />
      : <p role="status">{message}</p>}
  </div>;
}

function AccountRecordings({ accountId }: { accountId: number }) {
  const report = useImbalanceReport(accountId);
  return <>
    <TopBotTradeRecorder accountId={accountId} />
    <RecordedSignalReplay accountId={accountId} />
    <TopBotResearchStatus accountId={accountId} />
    {report.message && <p role="status">{report.message}</p>}
    {report.data && <SignalPanel data={report.data} now={report.checkedAt ?? Date.parse(report.data.as_of)} />}
    <TopBotComparison accountId={accountId} inclusion={report.data?.coverage} />
  </>;
}
