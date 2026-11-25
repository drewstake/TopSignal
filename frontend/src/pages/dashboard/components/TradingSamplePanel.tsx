import { useEffect, useState } from "react";
import { Card } from "../../../components/ui/Card";
import { accountsApi } from "../../../lib/api";
import { getAccountRiskRuleForAccount } from "../../../lib/accountRiskRules";
import { getTradeNetPnl } from "../../../lib/tradePnl";
import type { AccountInfo, AccountTrade } from "../../../lib/types";
import { formatCurrency, formatPercent, formatPnl } from "../../../utils/formatters";
import { hasPerformanceSample, PERFORMANCE_SAMPLE_MINIMUM } from "../../../utils/sustainability";
import { areCopyTradeMatches, isCopyTradeSelectableFollower } from "../copyTrade";

interface Props {
  account: AccountInfo;
  accounts: readonly AccountInfo[];
  copyIds: readonly number[];
  tradeCount: number;
  activeDays: number;
  singleTrade: AccountTrade | null;
  loading: boolean;
  error: string | null;
}

// Same-size closes on the same contract and side within two seconds are evidence
// of one copied decision. Ambiguous closes never count as confirmed matches.
function MatchedTrade({ trade, peerIds }: { trade: AccountTrade; peerIds: string }) {
  const [result, setResult] = useState<{ count: number; net: number; incomplete: boolean } | null>(null);
  useEffect(() => {
    let current = true;
    const close = Date.parse(trade.timestamp);
    if (!Number.isFinite(close)) return;
    const query = {
      start: new Date(close - 2000).toISOString(),
      end: new Date(close + 2000).toISOString(),
      limit: 3,
      includeLifecycle: false,
    };
    const ids = peerIds.split(",").map(Number);
    void Promise.allSettled(ids.map(id => accountsApi.getTrades(id, query))).then(results => {
      if (!current) return;
      let count = 1;
      let net = getTradeNetPnl(trade) ?? 0;
      let incomplete = false;
      for (const [index, response] of results.entries()) {
        if (response.status === "rejected") {
          incomplete = true;
          continue;
        }
        const matches = response.value.filter(row => row.account_id === ids[index] && areCopyTradeMatches(trade, row)
          && Math.abs(Date.parse(row.timestamp) - close) <= 2000 && getTradeNetPnl(row) !== null);
        if (response.value.length >= query.limit || matches.length > 1) {
          incomplete = true;
          continue;
        }
        if (matches.length === 1) {
          count += 1;
          net += getTradeNetPnl(matches[0])!;
        }
      }
      setResult({ count, net, incomplete });
    });
    return () => { current = false; };
  }, [peerIds, trade]);
  if (!result) return <p className="text-sm text-app-muted-text">Checking for matching fills across accounts…</p>;
  return <div>
    <p className="text-lg font-semibold text-app-text">{result.count > 1 ? `1 trade across ${result.count} accounts` : "1 matched account"}</p>
    <p className="text-sm text-app-muted-text">{result.net > 0 ? "+" : ""}{formatCurrency(result.net)} net on matched accounts · matched by contract, side, size and close time.{result.incomplete && " Some account matches could not be verified."}</p>
  </div>;
}

export function TradingSamplePanel({ account, accounts, copyIds, tradeCount, activeDays, singleTrade, loading, error }: Props) {
  const rule = getAccountRiskRuleForAccount(account);
  const freedom = rule?.planSize === "250k";
  const roster = accounts.filter(other => other.id === account.id || (copyIds.length > 0
    ? copyIds.includes(other.id)
    : freedom && !other.is_archived && isCopyTradeSelectableFollower(other) && getAccountRiskRuleForAccount(other)?.planSize === "250k"));
  const peers = roster.filter(other => other.id !== account.id).map(other => other.id).sort((a, b) => a - b).join(",");
  const freedomAccounts = freedom ? roster.filter(other => getAccountRiskRuleForAccount(other)?.planSize === "250k") : [];
  return <Card className="space-y-3 p-4" aria-label="Trading sample and combine progress">
    <h2 className="text-lg font-semibold text-app-text">Trading sample</h2>
    {error ? <p className="text-sm text-app-muted-text">Trading sample unavailable.</p> : loading ? <p className="text-sm text-app-muted-text">Loading trading sample…</p> : <>
      {tradeCount === 1 && singleTrade?.account_id === account.id && Number.isFinite(Date.parse(singleTrade.timestamp))
        && getTradeNetPnl(singleTrade) !== null && peers.length > 0 &&
        <MatchedTrade key={`${account.id}:${singleTrade.id}:${singleTrade.timestamp}:${peers}`} trade={singleTrade} peerIds={peers} />}
      <p className="text-sm text-app-muted-text">{tradeCount} closed {tradeCount === 1 ? "trade" : "trades"} on the selected account · {activeDays} trading {activeDays === 1 ? "day" : "days"} in range.
        {!hasPerformanceSample(tradeCount, activeDays) && ` Performance grades begin at ${PERFORMANCE_SAMPLE_MINIMUM} on this account.`}</p>
    </>}
    {freedom && <section className="space-y-2 border-t border-app-border pt-3">
      <h3 className="font-semibold text-app-text">$250K Freedom · current combine progress</h3>
      <p className="text-sm text-app-muted-text">{formatCurrency(rule.maxLossLimit)} end-of-day trailing loss limit · {formatCurrency(rule.dailyLossLimit!)} daily loss limit · {rule.consistencyTargetPercent}% consistency target</p>
      <div className="overflow-x-auto"><table className="w-full text-left text-sm">
        <thead className="text-app-muted-text"><tr>{["Account", "Net since start", "Base target", "Progress", "Remaining"].map(label => <th className="p-2" key={label}>{label}</th>)}</tr></thead>
        <tbody>{freedomAccounts.map(other => {
          const earned = !other.provider_data_stale && other.balance !== null && Number.isFinite(other.balance) ? other.balance - rule.nominalBuyingPower : null;
          return <tr key={other.id} className="border-t border-app-border">
            <th scope="row" className="p-2 font-medium">{other.name}</th>
            <td className="p-2">{earned === null ? "Balance unavailable" : formatPnl(earned)}</td>
            <td className="p-2">{formatCurrency(rule.profitTarget)}</td>
            <td className="p-2">{earned === null ? "—" : formatPercent(100 * earned / rule.profitTarget)}</td>
            <td className="p-2">{earned === null ? "—" : formatCurrency(Math.max(0, rule.profitTarget - earned))}</td>
          </tr>;
        })}</tbody>
      </table></div>
    </section>}
  </Card>;
}
