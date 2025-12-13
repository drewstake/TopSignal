import type { TopstepTrade } from "../../api/trade";
import type { DayPoint, EquityPoint } from "../../types/metrics";

const TZ = "America/New_York";

function dayKeyFromISO(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString("en-CA", { timeZone: TZ }); // YYYY-MM-DD
}

function safeNum(n: unknown) {
  const v = Number(n);
  return Number.isFinite(v) ? v : 0;
}

function ms(iso: string) {
  const t = new Date(iso).getTime();
  return Number.isFinite(t) ? t : 0;
}

type Lot = { sign: 1 | -1; size: number; price: number; time: string };

export type RoundTripTrade = {
  key: string;

  contractId: string;
  direction: "Long" | "Short";
  size: number;

  entryTime: string;
  exitTime: string;
  entryPrice: number;
  exitPrice: number;

  grossPnl: number;
  fees: number;
  netPnl: number;

  durationMs: number;

  // set later (async) using bars + contract
  maeTicks?: number;
  mfeTicks?: number;
  maeDollars?: number;
  mfeDollars?: number;
  givebackDollars?: number;
  givebackPct?: number;
};

export type DashboardComputed = {
  totals: {
    grossPnl: number;
    fees: number;
    netPnl: number;

    realizedTrades: number; // profitAndLoss != null
    halfTurns: number; // profitAndLoss == null
    totalExecutions: number; // all rows (non-voided)

    totalContracts: number;

    wins: number;
    losses: number;
    breakeven: number;

    winRate: number;
    profitFactor: number;
    avgWin: number;
    avgLoss: number;
    avgWinLossRatio: number;

    dayWinRate: number;
    bestDayPctOfProfit: number;

    bestTrade: number;
    worstTrade: number;

    mostActiveDay: string | null;
    mostProfitableDay: string | null;
    leastProfitableDay: string | null;

    maxDrawdown: number;
    avgTradesPerDay: number;

    buyPct: number;
    sellPct: number;

    avgTradeDurationMs: number;
    avgWinDurationMs: number;
    avgLossDurationMs: number;
  };

  days: DayPoint[];
  equity: EquityPoint[];
  roundTrips: RoundTripTrade[];
};

export function computeDashboardFromTrades(tradesRaw: TopstepTrade[]): DashboardComputed {
  const trades = (tradesRaw || []).filter((t) => !t.voided);

  // ---- day + base totals (same as before)
  const dayMap = new Map<string, DayPoint>();

  let totalContracts = 0;
  let totalExecutions = 0;

  let realizedTrades = 0;
  let halfTurns = 0;

  let grossPnl = 0;
  let fees = 0;

  let wins = 0;
  let losses = 0;
  let breakeven = 0;

  let sumWins = 0;
  let sumLosses = 0;

  let bestTrade = Number.NEGATIVE_INFINITY;
  let worstTrade = Number.POSITIVE_INFINITY;

  let buys = 0;
  let sells = 0;

  for (const t of trades) {
    totalExecutions += 1;

    const size = safeNum(t.size);
    totalContracts += size;

    if (t.side === 1) buys += 1;
    else if (t.side === 0) sells += 1;

    const key = dayKeyFromISO(t.creationTimestamp);
    if (!dayMap.has(key)) {
      dayMap.set(key, {
        date: key,
        grossPnl: 0,
        fees: 0,
        netPnl: 0,
        trades: 0,
        contracts: 0,
        buys: 0,
        sells: 0,
      });
    }

    const d = dayMap.get(key)!;
    d.contracts += size;
    if (t.side === 1) d.buys += 1;
    else if (t.side === 0) d.sells += 1;

    const f = safeNum(t.fees);
    fees += f;
    d.fees += f;

    if (t.profitAndLoss === null || t.profitAndLoss === undefined) {
      halfTurns += 1;
      continue;
    }

    const p = safeNum(t.profitAndLoss);
    realizedTrades += 1;

    grossPnl += p;
    d.grossPnl += p;
    d.trades += 1;

    if (p > bestTrade) bestTrade = p;
    if (p < worstTrade) worstTrade = p;

    if (p > 0) {
      wins += 1;
      sumWins += p;
    } else if (p < 0) {
      losses += 1;
      sumLosses += p;
    } else {
      breakeven += 1;
    }
  }

  const days = Array.from(dayMap.values()).sort((a, b) => a.date.localeCompare(b.date));
  for (const d of days) d.netPnl = d.grossPnl - d.fees;

  const netPnl = grossPnl - fees;

  const denomTrades = wins + losses + breakeven;
  const winRate = denomTrades > 0 ? wins / denomTrades : 0;

  const profitFactor = sumLosses < 0 ? sumWins / Math.abs(sumLosses) : sumWins > 0 ? Infinity : 0;

  const avgWin = wins > 0 ? sumWins / wins : 0;
  const avgLoss = losses > 0 ? sumLosses / losses : 0;
  const avgWinLossRatio = avgLoss < 0 ? avgWin / Math.abs(avgLoss) : 0;

  const activeDays = days.length;
  const greenDays = days.filter((d) => d.netPnl > 0).length;
  const dayWinRate = activeDays > 0 ? greenDays / activeDays : 0;

  let mostActiveDay: string | null = null;
  let mostProfitableDay: string | null = null;
  let leastProfitableDay: string | null = null;

  let maxTradesInDay = -1;
  let maxPnlInDay = Number.NEGATIVE_INFINITY;
  let minPnlInDay = Number.POSITIVE_INFINITY;

  for (const d of days) {
    if (d.trades > maxTradesInDay) {
      maxTradesInDay = d.trades;
      mostActiveDay = d.date;
    }
    if (d.netPnl > maxPnlInDay) {
      maxPnlInDay = d.netPnl;
      mostProfitableDay = d.date;
    }
    if (d.netPnl < minPnlInDay) {
      minPnlInDay = d.netPnl;
      leastProfitableDay = d.date;
    }
  }

  const bestDayPctOfProfit = netPnl > 0 && maxPnlInDay > 0 ? maxPnlInDay / netPnl : 0;

  const equity: EquityPoint[] = [];
  let cum = 0;
  let peak = 0;
  let maxDrawdown = 0;

  for (const d of days) {
    cum += d.netPnl;
    if (cum > peak) peak = cum;

    const dd = peak - cum;
    if (dd > maxDrawdown) maxDrawdown = dd;

    equity.push({
      date: d.date,
      pnl: d.netPnl,
      equity: cum,
      drawdown: dd,
      trades: d.trades,
      contracts: d.contracts,
    });
  }

  const avgTradesPerDay = activeDays > 0 ? realizedTrades / activeDays : 0;

  const totalSides = buys + sells;
  const buyPct = totalSides > 0 ? buys / totalSides : 0;
  const sellPct = totalSides > 0 ? sells / totalSides : 0;

  // ---- reconstruct round-trip trades for duration + MAE/MFE later
  const byTime = [...trades].sort((a, b) => ms(a.creationTimestamp) - ms(b.creationTimestamp));

  const lotsByContract = new Map<string, Lot[]>();
  const roundTrips: RoundTripTrade[] = [];
  let seg = 0;

  function getLots(cid: string) {
    if (!lotsByContract.has(cid)) lotsByContract.set(cid, []);
    return lotsByContract.get(cid)!;
  }

  function netSign(lots: Lot[]) {
    let s = 0;
    for (const l of lots) s += l.sign * l.size;
    if (s > 0) return 1;
    if (s < 0) return -1;
    return 0;
  }

  for (const t of byTime) {
    const cid = t.contractId;
    const lots = getLots(cid);

    const size = safeNum(t.size);
    if (size <= 0) continue;

    const fillSign: 1 | -1 = t.side === 1 ? 1 : -1;
    const cur = netSign(lots);

    const closing = cur !== 0 && fillSign !== cur;
    const fillGross = t.profitAndLoss === null || t.profitAndLoss === undefined ? null : safeNum(t.profitAndLoss);
    const fillFees = safeNum(t.fees);

    if (!closing) {
      lots.push({ sign: fillSign, size, price: safeNum(t.price), time: t.creationTimestamp });
      continue;
    }

    // closing: match FIFO
    let remaining = size;
    const exitPrice = safeNum(t.price);
    const exitTime = t.creationTimestamp;

    while (remaining > 0 && lots.length > 0 && netSign(lots) === -fillSign) {
      const head = lots[0];
      const take = Math.min(remaining, head.size);

      const entryPrice = head.price;
      const entryTime = head.time;
      const direction: "Long" | "Short" = head.sign === 1 ? "Long" : "Short";

      const portion = take / size;
      const grossPart = fillGross === null ? 0 : fillGross * portion;
      const feesPart = fillFees * portion;
      const netPart = grossPart - feesPart;

      const durationMs = Math.max(0, ms(exitTime) - ms(entryTime));

      roundTrips.push({
        key: `${t.id}:${seg++}`,
        contractId: cid,
        direction,
        size: take,
        entryTime,
        exitTime,
        entryPrice,
        exitPrice,
        grossPnl: grossPart,
        fees: feesPart,
        netPnl: netPart,
        durationMs,
      });

      head.size -= take;
      remaining -= take;

      if (head.size <= 0) lots.shift();
    }

    // if reversal, open new lots with leftover
    if (remaining > 0) {
      lots.push({ sign: fillSign, size: remaining, price: exitPrice, time: exitTime });
    }
  }

  // duration aggregates
  const rtAll = roundTrips.filter((r) => r.durationMs > 0);
  const rtWins = roundTrips.filter((r) => r.netPnl > 0 && r.durationMs > 0);
  const rtLoss = roundTrips.filter((r) => r.netPnl < 0 && r.durationMs > 0);

  const avgTradeDurationMs = rtAll.length ? rtAll.reduce((s, r) => s + r.durationMs, 0) / rtAll.length : 0;
  const avgWinDurationMs = rtWins.length ? rtWins.reduce((s, r) => s + r.durationMs, 0) / rtWins.length : 0;
  const avgLossDurationMs = rtLoss.length ? rtLoss.reduce((s, r) => s + r.durationMs, 0) / rtLoss.length : 0;

  return {
    totals: {
      grossPnl,
      fees,
      netPnl,

      realizedTrades,
      halfTurns,
      totalExecutions,

      totalContracts,

      wins,
      losses,
      breakeven,

      winRate,
      profitFactor,
      avgWin,
      avgLoss,
      avgWinLossRatio,

      dayWinRate,
      bestDayPctOfProfit,

      bestTrade: bestTrade === Number.NEGATIVE_INFINITY ? 0 : bestTrade,
      worstTrade: worstTrade === Number.POSITIVE_INFINITY ? 0 : worstTrade,

      mostActiveDay,
      mostProfitableDay,
      leastProfitableDay,

      maxDrawdown,
      avgTradesPerDay,

      buyPct,
      sellPct,

      avgTradeDurationMs,
      avgWinDurationMs,
      avgLossDurationMs,
    },
    days,
    equity,
    roundTrips,
  };
}
