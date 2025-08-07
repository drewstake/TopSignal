// src/components/Dashboard/useDashboardLogic.ts

import { useState, useEffect, useCallback } from 'react'
import { loginKey } from '../../endpoints/auth'
import { searchAccounts } from '../../endpoints/account'
import { searchOpenPositions } from '../../endpoints/position'
import { searchTrades } from '../../endpoints/trade'
import { runStrategy } from '../../services/botRunner'
import type { TradeSignal } from '../../strategies/types'

// ———————————— Types ————————————
export interface Account {
  id: number
  name: string
  balance: number
}
export interface PnL {
  date: string
  pnl: number
}
export interface Trade {
  id: number
  orderId: number
  symbol: string
  entry: number
  exit: number
  pnl: number
  creationTimestamp: string
  voided: boolean
  originalProfitAndLoss: number | null
}
export interface Position {
  id: number
  accountId: number
  contractId: string
  creationTimestamp: string
  size: number
  averagePrice: number
}

// ————————— Logic Hook —————————
export function useDashboardLogic() {
  // Auth
  const [username, setUsername]     = useState('')
  const [apiKey, setApiKey]         = useState('')
  const [token, setToken]           = useState<string | null>(null)
  const [isLoggedIn, setIsLoggedIn] = useState(false)

  // Selections & Data
  const [accounts, setAccounts]               = useState<Account[]>([])
  const [selectedAccount, setSelectedAccount] = useState<Account | null>(null)
  const [bots]                                = useState<string[]>(['Mean Reversion', 'Momentum'])
  const [selectedBot, setSelectedBot]         = useState<string>('Mean Reversion')

  const [pnls, setPnls]           = useState<PnL[]>([])
  const [trades, setTrades]       = useState<Trade[]>([])
  const [positions, setPositions] = useState<Position[]>([])
  const [filter, setFilter]       = useState('All')
  const [isRunning, setIsRunning] = useState(false)
  const [priceHistory, setPriceHistory] = useState<number[]>([])

  // Trading Log
  const [tradingLog, setTradingLog] = useState<string[]>([])

  // Handlers
  const doLogin = useCallback(async () => {
    const res = await loginKey(username, apiKey)
    if (res.success && res.token) {
      setToken(res.token)
      setIsLoggedIn(true)
    }
  }, [username, apiKey])

  const refreshData = useCallback(() => {
    if (!token || !selectedAccount) return

    // fetch positions
    searchOpenPositions(token, selectedAccount.id)
      .then(r => r.success && Array.isArray(r.positions) && setPositions(r.positions))
      .catch(console.error)

    // fetch and map trades
    searchTrades(token, selectedAccount.id)
      .then(r => {
        if (!r.success || !Array.isArray(r.trades)) return
        const mapped: Trade[] = r.trades.map(t => ({
          id: t.id,
          orderId: t.orderId,
          symbol: t.contractId,
          entry: t.price,
          exit: t.price,
          pnl: (t.profitAndLoss ?? 0) - t.fees,
          creationTimestamp: t.creationTimestamp,
          voided: t.voided,
          originalProfitAndLoss: t.profitAndLoss
        }))
        setTrades(mapped)
        setPnls(
          mapped.map(m => ({
            date: new Date(m.creationTimestamp).toLocaleDateString(),
            pnl: m.pnl
          }))
        )
      })
      .catch(console.error)
  }, [token, selectedAccount])

  // Effects

  // 1) Load accounts after login
  useEffect(() => {
    if (isLoggedIn && token) {
      searchAccounts(token)
        .then(r => r.success && Array.isArray(r.accounts) && setAccounts(r.accounts))
        .catch(console.error)
    }
  }, [isLoggedIn, token])

  // 2) When accounts load or selection changes
  useEffect(() => {
    if (accounts.length && !selectedAccount) {
      setSelectedAccount(accounts[0])
    }
    if (selectedAccount) {
      refreshData()
    }
  }, [accounts, selectedAccount, refreshData])

  // 3) Reset filter when bot changes
  useEffect(() => {
    setFilter('All')
  }, [selectedBot])

  // 4) Log start/stop exactly once on toggle
  useEffect(() => {
    setTradingLog(logs => [
      ...logs,
      `${new Date().toLocaleTimeString()}: Bot ${isRunning ? 'started' : 'stopped'}`
    ])
  }, [isRunning])

  // 5) Bot runner: poll and log trades when running
  useEffect(() => {
    if (!isRunning) return
    const intervalId = window.setInterval(() => {
      // simulate or fetch latest price
      const latestPrice = Math.random() * 100 + 1000  // replace with real data
      setPriceHistory(prev => {
        const newHistory = [...prev, latestPrice]
        // run strategy on updated history
        const signal: TradeSignal | null = runStrategy(selectedBot, newHistory)
        if (signal && signal.action !== 'hold') {
          const msg = `${new Date().toLocaleTimeString()}: ${signal.action.toUpperCase()} at $${latestPrice.toFixed(2)} (conf ${signal.confidence.toFixed(2)})`
          setTradingLog(logs => [...logs, msg])
        }
        return newHistory
      })
    }, 1000)
    return () => {
      window.clearInterval(intervalId)
    }
  }, [isRunning, selectedBot])

  // --- Computed Metrics ---
  const realizedPnL    = pnls.reduce((sum, p) => sum + p.pnl, 0)
  const balance        = selectedAccount?.balance ?? 0
  const winRate        = trades.length
    ? Math.round((trades.filter(t => t.pnl > 0).length / trades.length) * 100)
    : 0
  const tradeCount     = new Set(
    trades
      .filter(t => !t.voided && t.originalProfitAndLoss != null)
      .map(t => t.orderId)
  ).size
  const openCount      = positions.length
  const filteredTrades = trades.filter(t => filter === 'All' || t.symbol.includes(filter))

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

    // trading log
    tradingLog,

    // metrics
    realizedPnL, balance, winRate, tradeCount, openCount, filteredTrades
  }
}
