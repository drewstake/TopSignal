// src/components/Dashboard/useDashboardLogic.ts
import { useState, useEffect, useCallback } from 'react';
import { loginKey } from '../../endpoints/auth';
import { searchAccounts } from '../../endpoints/account';
import { searchOpenPositions } from '../../endpoints/position';
import { searchTrades } from '../../endpoints/trade';

// ————— Types —————————————
export interface Account   { id: number; name: string; balance: number; }
export interface PnL       { date: string; pnl: number; }
export interface Trade     {
  id: number;
  orderId: number;
  symbol: string;
  entry: number;
  exit: number;
  pnl: number;
  creationTimestamp: string;
  voided: boolean;
  originalProfitAndLoss: number | null;
}
export interface Position  { id: number; accountId: number; contractId: string; creationTimestamp: string; size: number; averagePrice: number; }

// ————— Hook —————————————
export function useDashboardLogic() {
  // Auth
  const [username, setUsername]       = useState('');
  const [apiKey, setApiKey]           = useState('');
  const [token, setToken]             = useState<string | null>(null);
  const [isLoggedIn, setIsLoggedIn]   = useState(false);

  // Selections & raw data
  const [accounts, setAccounts]               = useState<Account[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<Account | null>(null);
  const [bots]     = useState<string[]>(['BotA','BotB']);
  const [selectedBot, setSelectedBot]         = useState<string>('BotA');

  const [pnls, setPnls]           = useState<PnL[]>([]);
  const [trades, setTrades]       = useState<Trade[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [filter, setFilter]       = useState('All');
  const [isRunning, setIsRunning] = useState(false);

  // — Login handler ——
  const doLogin = useCallback(async () => {
    const res = await loginKey(username, apiKey);
    if (res.success && res.token) {
      setToken(res.token);
      setIsLoggedIn(true);
    }
  }, [username, apiKey]);

  // — Data refresh ——
  const refreshData = useCallback(() => {
    if (!token || !selectedAccount) return;

    // Positions
    searchOpenPositions(token, selectedAccount.id)
      .then(r => {
        if (r.success && Array.isArray(r.positions)) {
          setPositions(r.positions);
        }
      })
      .catch(console.error);

    // Trades → map into our Trade[]
    searchTrades(token, selectedAccount.id)
      .then(r => {
        if (!r.success || !Array.isArray(r.trades)) return;
        const mapped: Trade[] = r.trades.map(t => ({
          id: t.id,
          orderId: t.orderId,
          symbol: t.contractId,
          entry: t.price,
          exit: t.price,
          // subtract fees:
          pnl: (t.profitAndLoss ?? 0) - t.fees,
          creationTimestamp: t.creationTimestamp,
          voided: t.voided,
          originalProfitAndLoss: t.profitAndLoss
        }));
        setTrades(mapped);

        // Build PnL over time
        const points: PnL[] = mapped.map(m => ({
          date: new Date(m.creationTimestamp).toLocaleDateString(),
          pnl: m.pnl
        }));
        setPnls(points);
      })
      .catch(console.error);
  }, [token, selectedAccount]);

  // — Effects ——
  // After login, fetch accounts
  useEffect(() => {
    if (isLoggedIn && token) {
      searchAccounts(token)
        .then(r => {
          if (r.success && Array.isArray(r.accounts)) {
            setAccounts(r.accounts);
          }
        })
        .catch(console.error);
    }
  }, [isLoggedIn, token]);

  // When accounts load or selection changes, trigger refresh
  useEffect(() => {
    if (accounts.length > 0 && !selectedAccount) {
      setSelectedAccount(accounts[0]);
    }
    if (selectedAccount) {
      refreshData();
    }
  }, [accounts, selectedAccount, refreshData]);

  // Reset filter when bot changes
  useEffect(() => {
    setFilter('All');
  }, [selectedBot]);

  // — Computed metrics ——
  const realizedPnL = pnls.reduce((sum, p) => sum + p.pnl, 0);
  const balance     = selectedAccount?.balance ?? 0;
  const winRate     = trades.length
    ? Math.round((trades.filter(t => t.pnl > 0).length / trades.length) * 100)
    : 0;
  const tradeCount  = new Set(
    trades
      .filter(t => !t.voided && t.originalProfitAndLoss != null)
      .map(t => t.orderId)
  ).size;
  const openCount   = positions.length;
  const filteredTrades = trades.filter(
    t => filter === 'All' || t.symbol.includes(filter)
  );

  return {
    // auth
    username, setUsername,
    apiKey,   setApiKey,
    isLoggedIn, doLogin,

    // selections
    accounts, selectedAccount, setSelectedAccount,
    bots,     selectedBot,     setSelectedBot,

    // data & controls
    pnls, trades, positions,
    filter, setFilter,
    isRunning, setIsRunning,
    refreshData,

    // metrics
    realizedPnL, balance, winRate, tradeCount, openCount, filteredTrades
  };
}
