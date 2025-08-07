import React from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem
} from '../ui/select';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer
} from 'recharts';
import { motion } from 'framer-motion';
import type {
  Account,
  PnL,
  Trade,
  Position
} from './useDashboardLogic';

export interface DashboardUIProps {
  // Auth
  username: string;
  setUsername: (v: string) => void;
  apiKey: string;
  setApiKey: (v: string) => void;
  isLoggedIn: boolean;
  doLogin: () => void;

  // Selections
  accounts: Account[];
  selectedAccount: Account | null;
  setSelectedAccount: (a: Account | null) => void;
  bots: string[];
  selectedBot: string;
  setSelectedBot: (b: string) => void;

  // Data & Controls
  pnls: PnL[];
  trades: Trade[];
  positions: Position[];
  filter: string;
  setFilter: (f: string) => void;
  isRunning: boolean;
  setIsRunning: (r: boolean) => void;
  refreshData: () => void;

  // Trading Log
  tradingLog: string[];

  // Metrics
  realizedPnL: number;
  balance: number;
  winRate: number;
  tradeCount: number;
  openCount: number;
  filteredTrades: Trade[];
}

export function DashboardUI({
  username, setUsername,
  apiKey, setApiKey,
  isLoggedIn, doLogin,

  accounts, selectedAccount, setSelectedAccount,
  bots, selectedBot, setSelectedBot,

  pnls, trades, positions,
  filter, setFilter,
  isRunning, setIsRunning, refreshData,

  tradingLog,

  realizedPnL, balance, winRate, tradeCount, openCount, filteredTrades
}: DashboardUIProps) {
  if (!isLoggedIn) {
    return (
      <div className="flex items-center justify-center h-screen bg-gray-50">
        <Card className="w-full max-w-md">
          <CardHeader><CardTitle>Login</CardTitle></CardHeader>
          <CardContent>
            <Input
              placeholder="Username"
              value={username}
              onChange={e => setUsername(e.target.value)}
            />
            <Input
              placeholder="API Key"
              type="password"
              value={apiKey}
              onChange={e => setApiKey(e.target.value)}
            />
            <Button className="w-full mt-4" onClick={doLogin}>
              Login
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 bg-gray-50 min-h-screen">
      {/* Controls */}
      <motion.div
        initial={{ y: -20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.3 }}
        className="flex gap-4"
      >
        <Select
          value={selectedAccount?.name || ''}
          onValueChange={val => {
            const acct = accounts.find(a => a.name === val) || null;
            setSelectedAccount(acct);
          }}
        >
          <SelectTrigger className="w-48">
            <SelectValue placeholder="Account" />
          </SelectTrigger>
          <SelectContent>
            {accounts.map(a => (
              <SelectItem key={a.id} value={a.name}>
                {a.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={selectedBot} onValueChange={setSelectedBot}>
          <SelectTrigger className="w-48">
            <SelectValue placeholder="Bot" />
          </SelectTrigger>
          <SelectContent>
            {bots.map(b => (
              <SelectItem key={b} value={b}>
                {b}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Button onClick={() => setIsRunning(true)} disabled={isRunning}>
          Start Bot
        </Button>
        <Button onClick={() => setIsRunning(false)} disabled={!isRunning}>
          Stop Bot
        </Button>
        <Button onClick={refreshData}>Refresh</Button>
      </motion.div>

      {/* Metrics */}
      <div className="grid grid-cols-5 gap-4">
        <Card>
          <CardHeader><CardTitle>RP&amp;L</CardTitle></CardHeader>
          <CardContent>
            <span className="text-xl font-bold">${realizedPnL.toFixed(2)}</span>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>BAL</CardTitle></CardHeader>
          <CardContent>
            <span className="text-xl font-bold">${balance.toFixed(2)}</span>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Trades</CardTitle></CardHeader>
          <CardContent>
            <span className="text-xl font-bold">{tradeCount}</span>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Win Rate</CardTitle></CardHeader>
          <CardContent>
            <span className="text-xl font-bold">{winRate}%</span>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Open Pos</CardTitle></CardHeader>
          <CardContent>
            <span className="text-xl font-bold">{openCount}</span>
          </CardContent>
        </Card>
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={pnls}>
          <XAxis dataKey="date" />
          <YAxis />
          <Tooltip />
          <CartesianGrid strokeDasharray="3 3" />
          <Line dataKey="pnl" strokeWidth={3} dot={{ r: 5 }} />
        </LineChart>
      </ResponsiveContainer>

      {/* Trade Table */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div>
          <Input
            placeholder="Filter symbol"
            value={filter}
            onChange={e => setFilter(e.target.value)}
            className="mb-2"
          />
          <table className="w-full text-left divide-y">
            <thead className="bg-gray-100">
              <tr>
                <th>ID</th><th>Symbol</th><th>Entry</th><th>Exit</th><th>PNL</th>
              </tr>
            </thead>
            <tbody>
              {filteredTrades.map((t, i) => (
                <tr key={t.id} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                  <td className="py-2 px-1">{t.id}</td>
                  <td className="py-2 px-1">{t.symbol}</td>
                  <td className="py-2 px-1">{t.entry}</td>
                  <td className="py-2 px-1">{t.exit}</td>
                  <td className="py-2 px-1">${t.pnl}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {/* Trading Log */}
        <Card className="h-48 overflow-auto">
          <CardHeader><CardTitle>Trading Log</CardTitle></CardHeader>
          <CardContent>
            <ul className="text-sm space-y-1">
              {tradingLog.map((msg, i) => (
                <li key={i} className="whitespace-pre-wrap">{msg}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
