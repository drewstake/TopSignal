/// <reference types="node" />
/* global process */

// Liquidity sweep bot with real order placement against Project X.
// Env: PROJECTX_JWT, PROJECTX_ACCOUNT_ID, optional PROJECTX_CONTRACT_ID, PROJECTX_TICK_SIZE, PROJECTX_QTY, PROJECTX_RISK_POINTS, PROJECTX_REWARD_POINTS, PROJECTX_POLL_MS

type Bar = { t: string; o: number; h: number; l: number; c: number; v: number };

type RetrieveBarsRequest = {
  contractId: string | number;
  live: boolean;
  startTime: string; // ISO
  endTime: string; // ISO
  unit: number; // 1 sec, 2 min, 3 hour, 4 day
  unitNumber: number;
  limit: number;
  includePartialBar: boolean;
};

type RetrieveBarsResponse = {
  bars: Bar[];
  success: boolean;
  errorCode: number;
  errorMessage: string | null;
};

type Timeframe = {
  name: "15m" | "1h" | "4h" | "1d";
  unit: number;
  unitNumber: number;
  minutes: number;
};

type SweepSide = "long" | "short";

type SweepSignal = {
  id: string;
  tf: Timeframe["name"];
  side: SweepSide;

  levelPrice: number;
  levelTime: string;

  sweepTime: string;
  sweepExtreme: number;
  sweepClose: number;

  gapBars: number;
  clockDiffMinET: number;

  score: number;

  createdAt: string;
  expiresAt: string;
  status: "pending" | "ordered" | "expired";
};

type OrderSide = 0 | 1; // 0 buy/bid, 1 sell/ask
type OrderType = 1 | 2 | 4 | 5 | 6 | 7;

type PlaceOrderResponse = {
  orderId?: number;
  success: boolean;
  errorCode: number;
  errorMessage: string | null;
};

type BotConfig = {
  apiBase: string;
  token: string;
  accountId: number;
  contractId: string | number;
  live: boolean;
  tickSize: number;
  orderTag?: string;

  scanTfs: Timeframe[];
  entryTf: Timeframe;
  lookbackBarsByTf: Record<Timeframe["name"], number>;
  swingLeftByTf: Record<Timeframe["name"], number>;
  swingRightByTf: Record<Timeframe["name"], number>;
  minBreachPointsByTf: Record<Timeframe["name"], number>;
  minReclaimPointsByTf: Record<Timeframe["name"], number>;
  ageBandBarsByTf: Record<
    Timeframe["name"],
    { min: number; max: number; idealMin: number; idealMax: number }
  >;
  clockBoost: { windowMin: number; weight: number };
  revisit: { maxEntryBarsAfterSweep: number; touchTolerancePoints: number };
  order: { qty: number; riskPoints: number; rewardPoints: number };
  pollMs: number;
};

const TF_15M: Timeframe = { name: "15m", unit: 2, unitNumber: 15, minutes: 15 };
const TF_1H: Timeframe = { name: "1h", unit: 3, unitNumber: 1, minutes: 60 };
const TF_4H: Timeframe = { name: "4h", unit: 3, unitNumber: 4, minutes: 240 };
const TF_1D: Timeframe = { name: "1d", unit: 4, unitNumber: 1, minutes: 1440 };

function isoNow() {
  return new Date().toISOString();
}

function addMinutesISO(iso: string, minutes: number) {
  const d = new Date(iso);
  d.setMinutes(d.getMinutes() + minutes);
  return d.toISOString();
}

function sleep(ms: number) {
  return new Promise<void>((r) => setTimeout(r, ms));
}

function toNum(x: unknown) {
  return typeof x === "number" ? x : Number(x);
}

function sortBarsAsc(bars: Bar[]) {
  return [...bars].sort((a, b) => new Date(a.t).getTime() - new Date(b.t).getTime());
}

function minutesOfDayET(d: Date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(d);

  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? "0");
  const minute = Number(parts.find((p) => p.type === "minute")?.value ?? "0");
  return hour * 60 + minute;
}

function wrappedMinuteDiff(a: number, b: number) {
  const diff = Math.abs(a - b);
  return Math.min(diff, 1440 - diff);
}

async function retrieveBars(apiBase: string, token: string, req: RetrieveBarsRequest): Promise<Bar[]> {
  const res = await fetch(`${apiBase}/api/History/retrieveBars`, {
    method: "POST",
    headers: {
      accept: "text/plain",
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(req),
  });

  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`retrieveBars failed: ${res.status} ${res.statusText} ${txt}`);
  }

  const data = (await res.json()) as RetrieveBarsResponse;
  if (!data.success) {
    throw new Error(`retrieveBars error ${data.errorCode}: ${data.errorMessage ?? "unknown"}`);
  }

  return data.bars.map((b) => ({
    t: b.t,
    o: toNum(b.o),
    h: toNum(b.h),
    l: toNum(b.l),
    c: toNum(b.c),
    v: toNum(b.v),
  }));
}

function findSwingLows(bars: Bar[], left: number, right: number) {
  const swings: Array<{ idx: number; price: number; time: string }> = [];
  for (let i = left; i < bars.length - right; i++) {
    const cur = bars[i].l;
    let ok = true;
    for (let j = i - left; j <= i + right; j++) {
      if (j === i) continue;
      if (bars[j].l <= cur) {
        ok = false;
        break;
      }
    }
    if (ok) swings.push({ idx: i, price: cur, time: bars[i].t });
  }
  return swings;
}

function findSwingHighs(bars: Bar[], left: number, right: number) {
  const swings: Array<{ idx: number; price: number; time: string }> = [];
  for (let i = left; i < bars.length - right; i++) {
    const cur = bars[i].h;
    let ok = true;
    for (let j = i - left; j <= i + right; j++) {
      if (j === i) continue;
      if (bars[j].h >= cur) {
        ok = false;
        break;
      }
    }
    if (ok) swings.push({ idx: i, price: cur, time: bars[i].t });
  }
  return swings;
}

function ageScoreFromBand(
  gapBars: number,
  band: { min: number; max: number; idealMin: number; idealMax: number }
) {
  if (gapBars < band.min || gapBars > band.max) return 0;

  if (gapBars >= band.idealMin && gapBars <= band.idealMax) return 1;

  if (gapBars < band.idealMin) {
    const span = Math.max(1, band.idealMin - band.min);
    return (gapBars - band.min) / span;
  }

  const span = Math.max(1, band.max - band.idealMax);
  return (band.max - gapBars) / span;
}

function rejectionScoreLong(bar: Bar) {
  const range = Math.max(1e-9, bar.h - bar.l);
  const wick = Math.min(bar.o, bar.c) - bar.l;
  return Math.max(0, Math.min(1, wick / range));
}

function rejectionScoreShort(bar: Bar) {
  const range = Math.max(1e-9, bar.h - bar.l);
  const wick = bar.h - Math.max(bar.o, bar.c);
  return Math.max(0, Math.min(1, wick / range));
}

function makeId(parts: Array<string | number>) {
  return parts.join("|");
}

function computeExpiresAtFromEntryBars(entryTf: Timeframe, fromISO: string, barsAhead: number) {
  return addMinutesISO(fromISO, barsAhead * entryTf.minutes);
}

function touchesLevel(bar: Bar, price: number, tol: number) {
  return bar.l <= price + tol && bar.h >= price - tol;
}

function isExpired(nowISO: string, expiresISO: string) {
  return new Date(nowISO).getTime() > new Date(expiresISO).getTime();
}

function ticksFromPoints(points: number, tickSize: number) {
  if (!Number.isFinite(tickSize) || tickSize <= 0) {
    throw new Error("tickSize must be a positive number");
  }
  return Math.max(1, Math.round(Math.abs(points / tickSize)));
}

async function placeBracketLimitOrder(args: {
  cfg: BotConfig;
  side: SweepSide;
  limitPrice: number;
  stopPrice: number;
  targetPrice: number;
}): Promise<{ ok: true; orderId: string } | { ok: false; error: string }> {
  const { cfg, side, limitPrice, stopPrice, targetPrice } = args;
  const stopTicks = ticksFromPoints(limitPrice - stopPrice, cfg.tickSize);
  const targetTicks = ticksFromPoints(targetPrice - limitPrice, cfg.tickSize);

  const requestBody = {
    accountId: cfg.accountId,
    contractId: cfg.contractId,
    type: 2 as OrderType, // Limit entry
    side: side === "long" ? (0 as OrderSide) : (1 as OrderSide),
    size: cfg.order.qty,
    limitPrice,
    stopPrice: null,
    trailPrice: null,
    customTag: cfg.orderTag ?? "liquidity-sweep-bot",
    stopLossBracket: { ticks: stopTicks, type: 4 as OrderType },
    takeProfitBracket: { ticks: targetTicks, type: 2 as OrderType },
  };

  const res = await fetch(`${cfg.apiBase}/api/Order/place`, {
    method: "POST",
    headers: {
      accept: "text/plain",
      "content-type": "application/json",
      authorization: `Bearer ${cfg.token}`,
    },
    body: JSON.stringify(requestBody),
  });

  const text = await res.text();
  if (!res.ok) {
    return { ok: false, error: `placeOrder failed: HTTP ${res.status} ${res.statusText} ${text}` };
  }

  let data: PlaceOrderResponse | null = null;
  try {
    data = text ? (JSON.parse(text) as PlaceOrderResponse) : null;
  } catch {
    return { ok: false, error: `placeOrder unexpected body: ${text}` };
  }

  if (!data?.success) {
    return {
      ok: false,
      error: `placeOrder error ${data?.errorCode ?? "?"}: ${data?.errorMessage ?? "unknown"}`,
    };
  }

  return { ok: true, orderId: data.orderId ? String(data.orderId) : "unknown" };
}

class LiquiditySweepBot {
  private cfg: BotConfig;

  private seenSweepIds = new Set<string>();

  private signals: SweepSignal[] = [];

  private lastBarTime: Partial<Record<Timeframe["name"], string>> = {};

  constructor(cfg: BotConfig) {
    this.cfg = cfg;
    if (!Number.isFinite(cfg.tickSize) || cfg.tickSize <= 0) {
      throw new Error("tickSize must be positive");
    }
  }

  private async fetchTfBars(tf: Timeframe) {
    const now = isoNow();
    const lookback = this.cfg.lookbackBarsByTf[tf.name];
    const start = addMinutesISO(now, -lookback * tf.minutes);

    const bars = await retrieveBars(this.cfg.apiBase, this.cfg.token, {
      contractId: this.cfg.contractId,
      live: this.cfg.live,
      startTime: start,
      endTime: now,
      unit: tf.unit,
      unitNumber: tf.unitNumber,
      limit: lookback,
      includePartialBar: false,
    });

    return sortBarsAsc(bars);
  }

  private detectSweeps(tf: Timeframe, bars: Bar[]) {
    const left = this.cfg.swingLeftByTf[tf.name];
    const right = this.cfg.swingRightByTf[tf.name];
    const minBreach = this.cfg.minBreachPointsByTf[tf.name];
    const minReclaim = this.cfg.minReclaimPointsByTf[tf.name];
    const band = this.cfg.ageBandBarsByTf[tf.name];

    const swingsLow = findSwingLows(bars, left, right);
    const swingsHigh = findSwingHighs(bars, left, right);

    const out: SweepSignal[] = [];

    for (const sw of swingsLow) {
      for (let k = sw.idx + 1; k < bars.length; k++) {
        const gapBars = k - sw.idx;
        if (gapBars < band.min) continue;
        if (gapBars > band.max) break;

        const bar = bars[k];

        const breached = bar.l < sw.price - minBreach;
        const reclaimed = bar.c > sw.price + minReclaim;
        if (!breached || !reclaimed) continue;

        const t1 = new Date(sw.time);
        const t2 = new Date(bar.t);
        const clockDiff = wrappedMinuteDiff(minutesOfDayET(t1), minutesOfDayET(t2));

        const age = ageScoreFromBand(gapBars, band);
        const clockBoost =
          clockDiff <= this.cfg.clockBoost.windowMin
            ? (1 - clockDiff / this.cfg.clockBoost.windowMin) * this.cfg.clockBoost.weight
            : 0;

        const rej = rejectionScoreLong(bar);

        const depth = Math.min(1, (sw.price - bar.l) / Math.max(1e-9, minBreach * 4));

        const score = 1.2 * age + clockBoost + 0.8 * rej + 0.5 * depth;

        const id = makeId(["sweep", tf.name, "long", sw.time, sw.price, bar.t]);

        out.push({
          id,
          tf: tf.name,
          side: "long",
          levelPrice: sw.price,
          levelTime: sw.time,
          sweepTime: bar.t,
          sweepExtreme: bar.l,
          sweepClose: bar.c,
          gapBars,
          clockDiffMinET: clockDiff,
          score,
          createdAt: isoNow(),
          expiresAt: computeExpiresAtFromEntryBars(
            this.cfg.entryTf,
            bar.t,
            this.cfg.revisit.maxEntryBarsAfterSweep
          ),
          status: "pending",
        });

        break;
      }
    }

    for (const sw of swingsHigh) {
      for (let k = sw.idx + 1; k < bars.length; k++) {
        const gapBars = k - sw.idx;
        if (gapBars < band.min) continue;
        if (gapBars > band.max) break;

        const bar = bars[k];

        const breached = bar.h > sw.price + minBreach;
        const reclaimed = bar.c < sw.price - minReclaim;
        if (!breached || !reclaimed) continue;

        const t1 = new Date(sw.time);
        const t2 = new Date(bar.t);
        const clockDiff = wrappedMinuteDiff(minutesOfDayET(t1), minutesOfDayET(t2));

        const age = ageScoreFromBand(gapBars, band);
        const clockBoost =
          clockDiff <= this.cfg.clockBoost.windowMin
            ? (1 - clockDiff / this.cfg.clockBoost.windowMin) * this.cfg.clockBoost.weight
            : 0;

        const rej = rejectionScoreShort(bar);
        const depth = Math.min(1, (bar.h - sw.price) / Math.max(1e-9, minBreach * 4));

        const score = 1.2 * age + clockBoost + 0.8 * rej + 0.5 * depth;

        const id = makeId(["sweep", tf.name, "short", sw.time, sw.price, bar.t]);

        out.push({
          id,
          tf: tf.name,
          side: "short",
          levelPrice: sw.price,
          levelTime: sw.time,
          sweepTime: bar.t,
          sweepExtreme: bar.h,
          sweepClose: bar.c,
          gapBars,
          clockDiffMinET: clockDiff,
          score,
          createdAt: isoNow(),
          expiresAt: computeExpiresAtFromEntryBars(
            this.cfg.entryTf,
            bar.t,
            this.cfg.revisit.maxEntryBarsAfterSweep
          ),
          status: "pending",
        });

        break;
      }
    }

    out.sort((a, b) => b.score - a.score);
    return out;
  }

  private addSignals(newSignals: SweepSignal[]) {
    for (const s of newSignals) {
      if (this.seenSweepIds.has(s.id)) continue;
      this.seenSweepIds.add(s.id);
      this.signals.push(s);
      console.log(
        `[${s.tf}] NEW ${s.side.toUpperCase()} SWEEP level=${s.levelPrice.toFixed(
          2
        )} sweepTime=${s.sweepTime} score=${s.score.toFixed(2)} gapBars=${s.gapBars} clockDiffMinET=${
          s.clockDiffMinET
        }`
      );
    }
  }

  private expireSignals() {
    const now = isoNow();
    for (const s of this.signals) {
      if (s.status !== "pending") continue;
      if (isExpired(now, s.expiresAt)) {
        s.status = "expired";
        console.log(`EXPIRE ${s.side.toUpperCase()} level=${s.levelPrice.toFixed(2)} from ${s.sweepTime}`);
      }
    }
    this.signals = this.signals.filter((s) => s.status !== "expired");
  }

  private async maybeEnterOnRevisit(entryBars: Bar[]) {
    const last = entryBars[entryBars.length - 1];
    if (!last) return;

    const now = isoNow();
    const tol = this.cfg.revisit.touchTolerancePoints;

    for (const s of this.signals) {
      if (s.status !== "pending") continue;
      if (isExpired(now, s.expiresAt)) {
        s.status = "expired";
        continue;
      }

      if (!touchesLevel(last, s.levelPrice, tol)) continue;

      const entry = s.levelPrice;
      const risk = this.cfg.order.riskPoints;
      const reward = this.cfg.order.rewardPoints;

      const stop = s.side === "long" ? entry - risk : entry + risk;
      const target = s.side === "long" ? entry + reward : entry - reward;

      const res = await placeBracketLimitOrder({
        cfg: this.cfg,
        side: s.side,
        limitPrice: entry,
        stopPrice: stop,
        targetPrice: target,
      });

      if (!res.ok) {
        console.log(`ORDER FAIL: ${res.error}`);
        continue;
      }

      s.status = "ordered";
      console.log(
        `ORDERED ${s.side.toUpperCase()} limit=${entry.toFixed(2)} stop=${stop.toFixed(
          2
        )} target=${target.toFixed(2)} orderId=${res.orderId}`
      );
    }

    this.signals = this.signals.filter((s) => s.status === "pending");
  }

  private newClosedBarsSince(tf: Timeframe, bars: Bar[]) {
    const lastSeen = this.lastBarTime[tf.name];
    if (!lastSeen) return bars;
    const lastSeenMs = new Date(lastSeen).getTime();
    return bars.filter((b) => new Date(b.t).getTime() > lastSeenMs);
  }

  private updateLastBarTime(tf: Timeframe, bars: Bar[]) {
    const last = bars[bars.length - 1];
    if (last) this.lastBarTime[tf.name] = last.t;
  }

  async runForever() {
    console.log("Sweep bot starting...");
    while (true) {
      try {
        for (const tf of this.cfg.scanTfs) {
          const bars = await this.fetchTfBars(tf);

          const newBars = this.newClosedBarsSince(tf, bars);
          if (newBars.length === 0) continue;

          const sweeps = this.detectSweeps(tf, bars);
          this.addSignals(sweeps);

          this.updateLastBarTime(tf, bars);
        }

        const entryBars = await this.fetchTfBars(this.cfg.entryTf);

        const newEntryBars = this.newClosedBarsSince(this.cfg.entryTf, entryBars);
        if (newEntryBars.length > 0) {
          await this.maybeEnterOnRevisit(entryBars);
          this.updateLastBarTime(this.cfg.entryTf, entryBars);
        }

        this.expireSignals();
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        console.log("Loop error:", msg);
      }

      await sleep(this.cfg.pollMs);
    }
  }
}

function requiredNumber(envValue: string | undefined, name: string, fallback?: number) {
  const source = envValue === undefined ? fallback : Number(envValue);
  if (!Number.isFinite(source)) {
    throw new Error(`Set ${name} as a number`);
  }
  return source;
}

async function main() {
  const token = process.env.PROJECTX_JWT || "";
  if (!token) throw new Error("Set PROJECTX_JWT in your environment.");

  const accountId = Number(process.env.PROJECTX_ACCOUNT_ID ?? NaN);
  if (!Number.isFinite(accountId)) throw new Error("Set PROJECTX_ACCOUNT_ID in your environment.");

  const contractId = process.env.PROJECTX_CONTRACT_ID || "CON.F.US.MNQ.H26";
  const tickSize = requiredNumber(process.env.PROJECTX_TICK_SIZE, "PROJECTX_TICK_SIZE", 0.25);
  const qty = requiredNumber(process.env.PROJECTX_QTY, "PROJECTX_QTY", 1);
  const riskPoints = requiredNumber(process.env.PROJECTX_RISK_POINTS, "PROJECTX_RISK_POINTS", 100);
  const rewardPoints = requiredNumber(process.env.PROJECTX_REWARD_POINTS, "PROJECTX_REWARD_POINTS", 100);
  const pollMs = requiredNumber(process.env.PROJECTX_POLL_MS, "PROJECTX_POLL_MS", 30_000);

  if (qty <= 0) throw new Error("PROJECTX_QTY must be > 0");
  if (riskPoints <= 0 || rewardPoints <= 0) throw new Error("PROJECTX_RISK_POINTS and PROJECTX_REWARD_POINTS must be > 0");
  if (pollMs <= 0) throw new Error("PROJECTX_POLL_MS must be > 0");

  const cfg: BotConfig = {
    apiBase: process.env.PROJECTX_API_BASE || "https://api.topstepx.com",
    token,
    accountId,
    contractId,
    live: process.env.PROJECTX_LIVE === "true",
    tickSize,
    orderTag: process.env.PROJECTX_ORDER_TAG,

    scanTfs: [TF_15M, TF_1H, TF_4H, TF_1D],
    entryTf: TF_15M,

    lookbackBarsByTf: {
      "15m": 2500,
      "1h": 2000,
      "4h": 1500,
      "1d": 1200,
    },

    swingLeftByTf: { "15m": 3, "1h": 2, "4h": 2, "1d": 2 },
    swingRightByTf: { "15m": 3, "1h": 2, "4h": 2, "1d": 2 },

    minBreachPointsByTf: { "15m": 2, "1h": 4, "4h": 8, "1d": 15 },
    minReclaimPointsByTf: { "15m": 1, "1h": 2, "4h": 4, "1d": 8 },

    ageBandBarsByTf: {
      "15m": { min: 12, max: 400, idealMin: 40, idealMax: 140 },
      "1h": { min: 3, max: 250, idealMin: 12, idealMax: 60 },
      "4h": { min: 2, max: 180, idealMin: 6, idealMax: 40 },
      "1d": { min: 2, max: 200, idealMin: 5, idealMax: 30 },
    },

    clockBoost: { windowMin: 45, weight: 0.6 },

    revisit: { maxEntryBarsAfterSweep: 200, touchTolerancePoints: 1 },

    order: { qty, riskPoints, rewardPoints },

    pollMs,
  };

  const bot = new LiquiditySweepBot(cfg);
  await bot.runForever();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
