import type { Trade } from "../../types/trades";
import { fmtISO, seededRand, timeBlock } from "./utils";

export function makeMockTrades(): Trade[] {
  const rng = seededRand(20251212);
  const instruments = ["MNQ", "MES", "MGC", "MCL"];
  const setups = ["Pullback", "Breakout", "Sweep", "Mean Reversion", "ORB"];
  const regimes: Trade["regime"][] = ["Trend", "Range", "HighVol", "LowVol"];

  const today = new Date();
  const daysBack = 75;

  const trades: Trade[] = [];

  for (let i = daysBack; i >= 0; i -= 1) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const date = fmtISO(d);

    if (rng() < 0.15) continue;

    const n = 2 + Math.floor(rng() * 9);
    for (let k = 0; k < n; k += 1) {
      const instrument = instruments[Math.floor(rng() * instruments.length)];
      const setup = setups[Math.floor(rng() * setups.length)];
      const direction: Trade["direction"] = rng() < 0.52 ? "Long" : "Short";
      const contracts = 1 + (rng() < 0.22 ? 1 : 0) + (rng() < 0.08 ? 1 : 0);

      const plannedRisk = 80 + Math.round(rng() * 140);

      const pWin =
        setup === "Pullback" ? 0.53 :
        setup === "Mean Reversion" ? 0.51 :
        setup === "Sweep" ? 0.49 :
        setup === "ORB" ? 0.47 : 0.48;

      const win = rng() < pWin;

      let rMult = win ? (0.6 + rng() * 1.9) : -(0.6 + rng() * 1.2);
      if (rng() < 0.06) rMult += win ? 1.2 : -0.9;

      const grossPnl = Math.round(plannedRisk * rMult);
      const fees = Number((2.2 + rng() * 5.2).toFixed(2));
      const slippage = Number((rng() < 0.65 ? 0 : rng() * 6).toFixed(2));
      const netPnl = Number((grossPnl - fees - slippage).toFixed(2));

      const durationMin = Math.max(1, Math.round(2 + rng() * 140));

      const mfe = Math.max(0, Math.round(plannedRisk * (0.6 + rng() * 1.2)));
      const mae = Math.max(0, Math.round(plannedRisk * (0.2 + rng() * 0.9)));
      const giveback = Math.round(Math.min(rng() * 0.7, 0.95) * mfe);

      const entryMin =
        rng() < 0.55 ? (9 * 60 + 30 + Math.round(rng() * 90)) :
        rng() < 0.85 ? (11 * 60 + Math.round(rng() * 180)) :
        (14 * 60 + Math.round(rng() * 110));

      const entryH = String(Math.floor(entryMin / 60)).padStart(2, "0");
      const entryM = String(entryMin % 60).padStart(2, "0");
      const entryTime = `${entryH}:${entryM}`;

      const exitMin = Math.min(entryMin + durationMin, 16 * 60 + 30);
      const exitH = String(Math.floor(exitMin / 60)).padStart(2, "0");
      const exitM = String(exitMin % 60).padStart(2, "0");
      const exitTime = `${exitH}:${exitM}`;

      const exitType: Trade["exitType"] = win
        ? (rng() < 0.55 ? "Target" : rng() < 0.82 ? "Manual" : "Time")
        : (rng() < 0.62 ? "Stop" : rng() < 0.84 ? "Manual" : "Time");

      const isScratch = Math.abs(netPnl) < 10;
      const finalExitType = isScratch ? "Breakeven" : exitType;

      trades.push({
        id: `${date}-${k}-${instrument}`,
        date,
        instrument,
        setup,
        direction,
        contracts,
        plannedRisk,
        grossPnl,
        fees,
        netPnl,
        durationMin,
        entryTime,
        exitTime,
        timeBlock: timeBlock(entryTime),
        win,
        mae,
        mfe,
        giveback,
        exitType: finalExitType,
        slippage,
        regime: regimes[Math.floor(rng() * regimes.length)],
      });
    }
  }

  // Sort so behavior metrics work
  trades.sort((a, b) => (a.date === b.date ? (a.entryTime < b.entryTime ? -1 : 1) : (a.date < b.date ? -1 : 1)));
  return trades;
}
