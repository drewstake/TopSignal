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

    maxIntradayDrawdown: number;
    avgDrawdown: number;
    avgDrawdownLengthDays: number;
    maxDrawdownLengthDays: number;
    avgTimeToRecoveryDays: number;
    profitPerDay: number;
    profitPerHour: number;
    expectancyPerTrade: number;
    tailRiskAvg: number;
    consistencyByWeek: number;
    maxConsecutiveWins: number;
    maxConsecutiveLosses: number;
    avgLosingStreak: number;

    // breakdowns for further analysis
    timeBlocks: { label: string; netPnl: number; trades: number }[];
    instruments: { contractId: string; netPnl: number; trades: number }[];
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

  let drawdownStartIdx = -1;
  let drawdownTrough = 0;
  const drawdownDepths: number[] = [];
  const drawdownLengths: number[] = [];
  const recoveryDurations: number[] = [];

  for (const d of days) {
    cum += d.netPnl;
    if (cum > peak) peak = cum;

    const dd = peak - cum;
    if (dd > maxDrawdown) maxDrawdown = dd;

    if (dd > 0 && drawdownStartIdx === -1) {
      drawdownStartIdx = equity.length;
      drawdownTrough = cum;
    }
    if (dd === 0 && drawdownStartIdx !== -1) {
      const depth = peak - drawdownTrough;
      drawdownDepths.push(depth);
      drawdownLengths.push(equity.length - drawdownStartIdx);
      recoveryDurations.push(equity.length - drawdownStartIdx);
      drawdownStartIdx = -1;
    }
    if (drawdownStartIdx !== -1 && cum < drawdownTrough) {
      drawdownTrough = cum;
    }

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

  // streaks and expectancy
  const realizedExecs = trades
    .filter((t) => t.profitAndLoss !== null && t.profitAndLoss !== undefined)
    .sort((a, b) => ms(a.creationTimestamp) - ms(b.creationTimestamp));
  let maxConsecutiveWins = 0;
  let maxConsecutiveLosses = 0;
  let curWins = 0;
  let curLosses = 0;
  const losingStreaks: number[] = [];

  for (const t of realizedExecs) {
    const p = safeNum(t.profitAndLoss) - safeNum(t.fees);
    if (p > 0) {
      curWins += 1;
      maxConsecutiveWins = Math.max(maxConsecutiveWins, curWins);
      if (curLosses > 0) losingStreaks.push(curLosses);
      curLosses = 0;
    } else if (p < 0) {
      curLosses += 1;
      maxConsecutiveLosses = Math.max(maxConsecutiveLosses, curLosses);
      if (curWins > 0) curWins = 0;
    } else {
      if (curLosses > 0) losingStreaks.push(curLosses);
      curWins = 0;
      curLosses = 0;
    }
  }
  if (curLosses > 0) losingStreaks.push(curLosses);

  const avgLosingStreak = losingStreaks.length
    ? losingStreaks.reduce((s, n) => s + n, 0) / losingStreaks.length
    : 0;

  const lossRate = denomTrades > 0 ? losses / denomTrades : 0;
  const expectancyPerTrade = winRate * avgWin - lossRate * Math.abs(avgLoss);

  // intraday drawdowns (per day)
  let maxIntradayDrawdown = 0;
  for (const d of days) {
    const dayTrades = realizedExecs.filter((t) => dayKeyFromISO(t.creationTimestamp) === d.date);
    if (!dayTrades.length) continue;

    let intradayCum = 0;
    let intradayPeak = 0;
    for (const t of dayTrades) {
      intradayCum += safeNum(t.profitAndLoss) - safeNum(t.fees);
      intradayPeak = Math.max(intradayPeak, intradayCum);
      const dd = intradayPeak - intradayCum;
      if (dd > maxIntradayDrawdown) maxIntradayDrawdown = dd;
    }
  }

  const avgDrawdown = drawdownDepths.length
    ? drawdownDepths.reduce((s, n) => s + n, 0) / drawdownDepths.length
    : 0;
  const avgDrawdownLengthDays = drawdownLengths.length
    ? drawdownLengths.reduce((s, n) => s + n, 0) / drawdownLengths.length
    : 0;
  const maxDrawdownLengthDays = drawdownLengths.length ? Math.max(...drawdownLengths) : 0;
  const avgTimeToRecoveryDays = recoveryDurations.length
    ? recoveryDurations.reduce((s, n) => s + n, 0) / recoveryDurations.length
    : 0;

  const profitPerDay = activeDays > 0 ? netPnl / activeDays : 0;
  const totalHoursInMarket = roundTrips.reduce((s, r) => s + r.durationMs, 0) / (1000 * 60 * 60);
  const profitPerHour = totalHoursInMarket > 0 ? netPnl / totalHoursInMarket : 0;

  // tail risk (average of worst 5% trades)
  const realizedPnls = realizedExecs.map((t) => safeNum(t.profitAndLoss) - safeNum(t.fees)).sort((a, b) => a - b);
  const tailCount = Math.max(1, Math.floor(realizedPnls.length * 0.05));
  const tailRiskAvg = realizedPnls.length
    ? realizedPnls.slice(0, tailCount).reduce((s, n) => s + n, 0) / tailCount
    : 0;

  // weekly consistency
  function isoWeekKey(dateStr: string) {
    const d = new Date(dateStr);
    const tmp = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
    const dayNum = tmp.getUTCDay() || 7;
    tmp.setUTCDate(tmp.getUTCDate() + 4 - dayNum);
    const yearStart = new Date(Date.UTC(tmp.getUTCFullYear(), 0, 1));
    const weekNo = Math.ceil(((tmp.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
    return `${tmp.getUTCFullYear()}-W${String(weekNo).padStart(2, "0")}`;
  }

  const weekly = new Map<string, number>();
  for (const d of days) {
    const key = isoWeekKey(d.date);
    weekly.set(key, (weekly.get(key) || 0) + d.netPnl);
  }
  const weekEntries = [...weekly.values()];
  const consistencyByWeek = weekEntries.length
    ? weekEntries.filter((v) => v > 0).length / weekEntries.length
    : 0;

  // time of day breakdown (America/New_York)
  function timeBlockLabel(ts: string) {
    const d = new Date(ts);
    const hour = d.toLocaleString("en-US", { hour: "2-digit", hour12: false, timeZone: TZ });
    const minute = d.toLocaleString("en-US", { minute: "2-digit", hour12: false, timeZone: TZ });
    const h = Number(hour);
    const m = Number(minute);
    const totalMinutes = h * 60 + m;
    if (totalMinutes < 9 * 60 + 30) return "Pre-market";
    if (totalMinutes <= 16 * 60) return "Regular";
    return "After hours";
  }

  const timeBlockMap = new Map<string, { netPnl: number; trades: number }>();
  const instrumentMap = new Map<string, { netPnl: number; trades: number }>();

  for (const t of realizedExecs) {
    const pnl = safeNum(t.profitAndLoss) - safeNum(t.fees);
    const block = timeBlockLabel(t.creationTimestamp);
    const tb = timeBlockMap.get(block) || { netPnl: 0, trades: 0 };
    tb.netPnl += pnl;
    tb.trades += 1;
    timeBlockMap.set(block, tb);

    const inst = instrumentMap.get(t.contractId) || { netPnl: 0, trades: 0 };
    inst.netPnl += pnl;
    inst.trades += 1;
    instrumentMap.set(t.contractId, inst);
  }

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

      maxIntradayDrawdown,
      avgDrawdown,
      avgDrawdownLengthDays,
      maxDrawdownLengthDays,
      avgTimeToRecoveryDays,
      profitPerDay,
      profitPerHour,
      expectancyPerTrade,
      tailRiskAvg,
      consistencyByWeek,
      maxConsecutiveWins,
      maxConsecutiveLosses,
      avgLosingStreak,

      timeBlocks: [...timeBlockMap.entries()].map(([label, v]) => ({ label, netPnl: v.netPnl, trades: v.trades })),
      instruments: [...instrumentMap.entries()].map(([contractId, v]) => ({
        contractId,
        netPnl: v.netPnl,
        trades: v.trades,
      })),
    },
    days,
    equity,
    roundTrips,
  };
}
