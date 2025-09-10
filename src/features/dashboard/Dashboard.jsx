import { useEffect, useState } from "react";
import { Activity, Shield, LogOut } from "lucide-react";
import Button from "../../components/ui/Button";
import { useAuth } from "../../context/AuthContext";
import { useAccounts } from "../../hooks/useAccounts";
import { useMarketPrice } from "../../hooks/useMarketPrice";
import { useOrders } from "../../hooks/useOrders";
import { usePositions } from "../../hooks/usePositions";
import { useAccountPnl } from "../../hooks/useAccountPnl";
import LockoutAllButton from "../lockout/LockoutAllButton";
import LockoutAccountButton from "../lockout/LockoutAccountButton";

import AccountCard from "./AccountCard";
import StrategyCard from "./StrategyCard";
import LiveMarketCard from "./LiveMarketCard";
import OrdersTable from "./OrdersTable";
import TradingLog from "./TradingLog";
import OrderTicket from "../trade/OrderTicket";
import PositionsCard from "../trade/PositionsCard";
import PnlCard from "../trade/PnlCard";

export default function Dashboard() {
  const { username, logout } = useAuth();

  // Accounts (active only)
  const { accounts } = useAccounts(true);
  const [selectedAccount, setSelectedAccount] = useState("");

  // Strategies (simple local list)
  const [strategies] = useState([
    { id: "strat-1", name: "Momentum" },
    { id: "strat-2", name: "Mean Reversion" },
    { id: "strat-3", name: "Breakout" },
    { id: "strat-4", name: "AI Scalper" },
  ]);
  const [selectedStrategy, setSelectedStrategy] = useState("strat-1");

  // Market price
  const CONTRACT_ID = import.meta.env.VITE_NQ_CONTRACT_ID;
  const { price, delta, deltaPct, feedSource } = useMarketPrice(CONTRACT_ID);

  // Orders + log + bot state
  const [logs, setLogs] = useState([]);
  const [botRunning, setBotRunning] = useState(false);
  const log = (msg) => setLogs((l) => [...l, { id: crypto.randomUUID(), t: Date.now(), msg }]);

  // Real orders + positions
  const { openOrders, loading: ordersLoading, place, cancel } = useOrders({
    accountId: selectedAccount,
    pollMs: 3000,
  });
  const { positions, loading: posLoading, close, partialClose } = usePositions({
    accountId: selectedAccount,
    pollMs: 5000,
  });
  const { unrealized, realized, loading: pnlLoading } = useAccountPnl({
    accountId: selectedAccount,
    pollMs: 5000,
  });

  // set defaults when accounts load
  useEffect(() => {
    if (accounts.length && !selectedAccount) {
      setSelectedAccount(String(accounts[0].id));
      log(`Loaded ${accounts.length} active accounts from API`);
    }
  }, [accounts]); // eslint-disable-line react-hooks/exhaustive-deps

  // mock bot behavior (unchanged)
  useEffect(() => {
    if (!botRunning) return;
    const h = setInterval(async () => {
      if (!selectedAccount) return;
      const side = Math.random() > 0.5 ? 0 : 1; // 0=Buy,1=Sell
      const qty = 1 + Math.floor(Math.random() * 3);
      const type = 2; // market
      try {
        const id = await place({
          accountId: Number(selectedAccount),
          contractId: CONTRACT_ID,
          type,
          side,
          size: qty,
        });
        log(`Bot placed ${side === 0 ? "Buy" : "Sell"} ${qty} @ market (order ${id})`);
      } catch (e) {
        log(`Bot place failed: ${e?.message || e}`);
      }
    }, 5000);
    return () => clearInterval(h);
  }, [botRunning, selectedAccount, CONTRACT_ID]); // eslint-disable-line react-hooks/exhaustive-deps

  const startBot = () => {
    if (!selectedAccount || !selectedStrategy) return;
    setBotRunning(true);
    const acc = accounts.find((a) => String(a.id) === selectedAccount)?.name;
    const strat = strategies.find((s) => s.id === selectedStrategy)?.name;
    log(`Bot started on ${acc} with ${strat}`);
  };
  const stopBot = () => {
    setBotRunning(false);
    log("Bot stopped");
  };

  async function onCancelOrder(orderId) {
    try {
      await cancel(orderId);
      log(`Cancelled order ${orderId}`);
    } catch (e) {
      alert(e?.message || "Cancel failed");
      log(`Cancel failed: ${e?.message || e}`);
    }
  }

  const currentAccountName =
    accounts.find((a) => String(a.id) === String(selectedAccount))?.name || "";

  return (
    <div className="min-h-screen w-full text-zinc-100 bg-[radial-gradient(1200px_800px_at_20%_-10%,rgba(99,102,241,0.25),transparent),radial-gradient(1200px_800px_at_100%_10%,rgba(236,72,153,0.2),transparent)] bg-zinc-950">
      {/* Top Bar */}
      <header className="sticky top-0 z-20 border-b border-white/10 backdrop-blur supports-[backdrop-filter]:bg-zinc-950/60">
        <div className="mx-auto max-w-7xl px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-8 w-8 rounded-xl bg-gradient-to-br from-indigo-500 to-fuchsia-600 shadow-lg" />
            <h1 className="text-lg font-semibold tracking-tight">TopSignal • Trading Bot Dashboard</h1>
            <span className="ml-3 text-xs text-zinc-400 hidden md:inline">Trading Enabled</span>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <div className="hidden md:flex items-center gap-2 text-zinc-300">
              <Activity className="h-4 w-4" />
              <span>NQ</span>
              <span className="font-medium">{price != null ? price.toFixed(2) : "-"}</span>
              <span className={`${Number(delta) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                {Number(delta) >= 0 ? "+" : ""}
                {Number(delta || 0).toFixed(2)} ({Number(deltaPct || 0).toFixed(2)}%)
              </span>
              <span className="ml-2 text-xs px-2 py-0.5 rounded-md bg-white/5 border border-white/10">
                {feedSource || "…"}
              </span>
            </div>
            <div className="flex items-center gap-2 text-zinc-300">
              <Shield className="h-4 w-4" />
              <span className="hidden sm:inline">Signed in:</span>
              <span className="font-medium">{username}</span>
            </div>

            {/* Lockout All (Today) for all XFA accounts */}
            <LockoutAllButton log={log} />

            <Button variant="ghost" className="ml-1 px-3 py-1.5" onClick={logout}>
              <LogOut className="h-4 w-4" />
              <span className="hidden sm:inline ml-1">Logout</span>
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8 grid grid-cols-12 gap-6">
        {/* LEFT: Controls */}
        <section className="col-span-12 lg:col-span-3 space-y-6">
          <AccountCard
            accounts={accounts}
            selectedAccount={selectedAccount}
            onChange={setSelectedAccount}
            onSwitch={(acc) => log(`Switched to account ${acc.name}`)}
          />

          {/* Single-account lockout for the currently selected account */}
          <div className="flex gap-3">
            <LockoutAccountButton
              accountId={selectedAccount}
              accountName={currentAccountName}
              log={log}
            />
          </div>

          <StrategyCard
            strategies={strategies}
            selectedStrategy={selectedStrategy}
            onChange={setSelectedStrategy}
            onStart={startBot}
            onStop={stopBot}
            botRunning={botRunning}
            disabled={!selectedAccount}
          />
          <PnlCard unrealized={unrealized} realized={realized} loading={pnlLoading} />
          <PositionsCard
            positions={positions}
            loading={posLoading}
            onClose={(cid) =>
              close(cid)
                .then(() => log(`Closed position ${cid}`))
                .catch((e) => {
                  alert(e?.message || "Close failed");
                  log(`Close failed: ${e?.message || e}`);
                })
            }
            onPartial={(cid, sz) =>
              partialClose(cid, sz)
                .then(() => log(`Partial close ${sz} on ${cid}`))
                .catch((e) => {
                  alert(e?.message || "Partial close failed");
                  log(`Partial close failed: ${e?.message || e}`);
                })
            }
          />
        </section>

        {/* CENTER: Price + OrderTicket + Orders */}
        <section className="col-span-12 lg:col-span-6 space-y-6">
          <LiveMarketCard price={price} delta={delta} deltaPct={deltaPct} />
          <OrderTicket
            accountId={selectedAccount}
            contractId={CONTRACT_ID}
            lastPrice={price}
            onPlace={place}
            onPlaced={(id) => log(`Order ${id} acknowledged`)}
            disabled={!selectedAccount}
            log={log}
          />
          <OrdersTable orders={openOrders} onCancel={onCancelOrder} refreshing={ordersLoading} />
        </section>

        {/* RIGHT: Trading Log */}
        <section className="col-span-12 lg:col-span-3 space-y-6">
          <TradingLog logs={logs} botRunning={botRunning} />
        </section>
      </main>
    </div>
  );
}
