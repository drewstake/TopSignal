// TopBot 2024 Adaptive — EXPERIMENTAL research indicator, FXReplay V2.
// MNQ, 5-minute candles. Manual backtest signals; does not place orders.
// Learns from past settled paths, NOT the frozen 2026 TopBot model.
// Defaults choose the better direction, even if both estimates are negative.
// No confidence/profitability claim. Keep the same loaded history between runs.
// Entry at CURRENT candle OPEN, using ONLY the preceding 21 CLOSED candles.
// Marker ends at entry time: do not enter on the preceding candle.
// Exit at first SL/TP touch, otherwise after 3 candles (15 minutes).
// Same-bar SL + TP: STOP wins. Gaps use open; exit costs remain conservative.
// Assumptions: MNQ $2/point, 0.5 point slippage per side, $0.61 fee per side.
// Training includes overnight bars. Entries are restricted to year 2024.
// RTH option: 09:35-15:30 New York; skips listed 2024 holiday/short sessions.
indicator({ onMainPanel: true, format: 'inherit' });
const memory = input.int('Recent training paths', 240, { min: 40, max: 500 });
const minimum = input.int('Warmup settled paths', 24, { min: 12, max: 40 });
const advantageHundredths = input.int('Minimum directional advantage (0.01R)', 5, { min: 0, max: 100 });
const positiveOnly = input.bool('Require positive estimated payoff', false);
const rthOnly = input.bool('Entries during regular hours only', true);
const showExits = input.bool('Show exit labels', true);
const showStatus = input.bool('Show current status', true);
const labelHistory = input.int('Label history (bars)', 600, { min: 50, max: 600 });
const historyRequest = ta.sma(close, 22);
var adaptive = { last: 0, samples: [], pending: [], active: null, nextEntry: 0,
  buys: 0, sells: 0, status: 'Warming up', buyScore: 0, sellScore: 0 };

// CORE START — the same functions are exercised by the offline tests.
function seconds(t) { return t > 100000000000 ? t / 1000 : t; }
function validBar(b) {
  return [b.t, b.o, b.h, b.l, b.c, b.v].every(Number.isFinite) &&
    Math.min(b.o, b.h, b.l, b.c) > 0 && b.v >= 0 &&
    b.l <= Math.min(b.o, b.c) && b.h >= Math.max(b.o, b.c) && b.t % 300 === 0;
}
function features(rows) {
  if (rows.length !== 21) return null;
  let squared = 0, recent = 0, momentum = 0;
  for (let i = 0; i < 21; i++) {
    if (!validBar(rows[i]) || (i > 0 && rows[i].t - rows[i - 1].t !== 300)) return null;
    if (i > 0) {
      const change = rows[i].c - rows[i - 1].c;
      squared += change * change;
      if (i >= 16) recent += change * change;
      if (i >= 18) momentum += change;
    }
  }
  const sigma = Math.sqrt(squared / 20);
  if (!(sigma > 0)) return null;
  const stop = Math.max(4, Math.ceil(sigma * Math.sqrt(3) * 4) / 4);
  return { stop: stop, target: Math.ceil(stop * 1.5 * 4) / 4,
    cell: (momentum >= 0 ? 2 : 0) + (recent / 5 >= squared / 20 ? 1 : 0) };
}
function entrySession(t, regular) {
  const utc = new Date(t * 1000), year = utc.getUTCFullYear();
  const marchFirst = new Date(Date.UTC(year, 2, 1)).getUTCDay();
  const novemberFirst = new Date(Date.UTC(year, 10, 1)).getUTCDay();
  const dstStart = Date.UTC(year, 2, 8 + (7 - marchFirst) % 7, 7) / 1000;
  const dstEnd = Date.UTC(year, 10, 1 + (7 - novemberFirst) % 7, 6) / 1000;
  const ny = new Date((t - (t >= dstStart && t < dstEnd ? 4 : 5) * 3600) * 1000);
  if (ny.getUTCFullYear() !== 2024) return false;
  if (!regular) return true;
  const day = ny.getUTCDay(), date = (ny.getUTCMonth() + 1) * 100 + ny.getUTCDate();
  const holidays = [101, 115, 219, 329, 527, 619, 703, 704, 902, 1128, 1129, 1224, 1225];
  const minute = ny.getUTCHours() * 60 + ny.getUTCMinutes();
  return day > 0 && day < 6 && holidays.indexOf(date) === -1 && minute >= 575 && minute <= 930;
}
function newLeg(side, entry, stop, target) {
  const fill = entry + side * 0.5;
  return { side: side, entry: fill, stop: fill - side * stop,
    target: fill + side * target, risk: stop, done: false, netR: 0,
    exit: 0, reason: '', age: 0 };
}
function settleLeg(leg, b) {
  if (leg.done) return;
  leg.age++;
  const s = leg.side, adverse = s === 1 ? b.l : b.h, favorable = s === 1 ? b.h : b.l;
  let fill = 0, reason = '';
  if (s * (b.o - leg.stop) <= 0) { fill = b.o; reason = 'STOP GAP'; }
  else if (s * (b.o - leg.target) >= 0) { fill = leg.target; reason = 'TARGET'; }
  else if (s * (adverse - leg.stop) <= 0) { fill = leg.stop; reason = 'STOP'; }
  else if (s * (favorable - leg.target) >= 0) { fill = leg.target; reason = 'TARGET'; }
  else if (leg.age >= 3) { fill = b.c; reason = 'TIME'; }
  if (reason !== '') {
    leg.done = true;
    leg.exit = fill - s * 0.5;
    leg.reason = reason;
    leg.netR = (s * (leg.exit - leg.entry) * 2 - 1.22) / (leg.risk * 2);
  }
}
function estimates(samples, cell) {
  let allBuy = 0, allSell = 0, cellBuy = 0, cellSell = 0, count = 0;
  for (let i = 0; i < samples.length; i++) {
    const p = samples[i];
    allBuy += p.buy; allSell += p.sell;
    if (p.cell === cell) { cellBuy += p.buy; cellSell += p.sell; count++; }
  }
  const n = Math.max(1, samples.length);
  return { buy: (cellBuy + 20 * allBuy / n) / (count + 20),
    sell: (cellSell + 20 * allSell / n) / (count + 20) };
}
function advance(state, rows, t, entry, options) {
  const result = { side: 0, entry: 0, stop: 0, target: 0, risk: 4,
    exit: 0, exitReason: '', exitR: 0 };
  if (t <= state.last) return result;
  state.last = t;
  const b = rows[rows.length - 1];
  if (!b || !validBar(b)) { state.pending = []; state.active = null; state.status = 'Invalid candle'; return result; }
  // Settle ONLY the preceding closed bar. Nothing reads current H/L/C/V.
  if (state.active) {
    const a = state.active;
    if (b.t !== a.next || t !== b.t + 300) {
      result.exitReason = 'DATA GAP - review trade';
      result.exit = b.c;
      state.active = null;
    } else {
      settleLeg(a.leg, b); a.next += 300;
      if (a.leg.done) {
        result.exit = a.leg.exit; result.exitReason = a.leg.reason;
        result.exitR = a.leg.netR; state.active = null;
      }
    }
  }
  const pending = [];
  for (let i = 0; i < state.pending.length; i++) {
    const p = state.pending[i];
    if (b.t !== p.next || t !== b.t + 300) continue;
    settleLeg(p.buy, b); settleLeg(p.sell, b);
    p.age++; p.next += 300;
    // Always wait the full horizon, including paths that stopped early.
    if (p.age === 3) {
      state.samples.push({ cell: p.cell, buy: p.buy.netR, sell: p.sell.netR });
      if (state.samples.length > options.memory) state.samples.shift();
    } else pending.push(p);
  }
  state.pending = pending;
  const f = features(rows);
  if (!f || t !== b.t + 300 || !Number.isFinite(entry) || entry <= 0) {
    state.status = 'Need 21 continuous 5m closed candles'; return result;
  }
  if (f.stop > 25) { state.status = 'Volatility: stop exceeds 25 points'; return result; }
  state.pending.push({ cell: f.cell, next: t, age: 0,
    buy: newLeg(1, entry, f.stop, f.target), sell: newLeg(-1, entry, f.stop, f.target) });
  if (state.samples.length < options.minimum) {
    state.status = 'Learning ' + state.samples.length + '/' + options.minimum; return result;
  }
  const score = estimates(state.samples, f.cell);
  state.buyScore = score.buy; state.sellScore = score.sell;
  if (!entrySession(t, options.regular)) { state.status = 'Outside 2024 entry session'; return result; }
  if (state.active || t < state.nextEntry) { state.status = 'Trade / 15m cooldown'; return result; }
  const difference = score.buy - score.sell, best = Math.max(score.buy, score.sell);
  if (Math.abs(difference) < options.advantage || difference === 0 || (options.positive && best <= 0)) {
    state.status = 'Waiting for directional advantage'; return result;
  }
  const side = difference > 0 ? 1 : -1;
  const leg = newLeg(side, entry, f.stop, f.target);
  state.active = { next: t, leg: leg }; state.nextEntry = t + 900;
  if (side === 1) state.buys++; else state.sells++;
  state.status = side === 1 ? 'BUY' : 'SELL';
  result.side = side; result.entry = leg.entry; result.stop = leg.stop;
  result.target = leg.target; result.risk = f.stop;
  return result;
}
// CORE END

if (index >= 21) {
  const rows = [];
  for (let offset = 21; offset >= 1; offset--) {
    rows.push({ t: seconds(time(offset)), o: open(offset), h: high(offset),
      l: low(offset), c: close(offset), v: volume(offset) });
  }
  const signal = advance(adaptive, rows, seconds(time(0)), open(0), {
    memory: memory, minimum: minimum, advantage: advantageHundredths / 100,
    positive: positiveOnly, regular: rthOnly
  });
  // At most ~400 entry/exit labels, below FXReplay's 500-drawing limit.
  const drawRecent = index >= candlesCount - labelHistory;
  if (signal.side !== 0 && drawRecent) {
    const isBuy = signal.side === 1, tint = isBuy ? '#00c878' : '#ef5350';
    const y = isBuy ? Math.min(low(1), open(0)) - signal.risk : Math.max(high(1), open(0)) + signal.risk;
    rectangle.create(time(1), y + 1, time(0), y - 1, {
      color: tint, fillColor: isBuy ? 'rgba(0,200,120,0.2)' : 'rgba(239,83,80,0.2)',
      text: (isBuy ? 'BUY' : 'SELL') + ' @ ' + signal.entry.toFixed(2) +
        ' | SL ' + signal.stop.toFixed(2) + ' | TP ' + signal.target.toFixed(2) + ' | max 15m'
    });
  }
  if (showExits && signal.exitReason !== '' && drawRecent) {
    rectangle.create(time(1), signal.exit + 1, time(0), signal.exit - 1, {
      color: '#ffca28', fillColor: 'rgba(255,202,40,0.12)',
      text: 'EXIT ' + signal.exitReason + ' @ ' + signal.exit.toFixed(2)
    });
  }
}
// Show setup/warmup guidance even before 21 candles have been evaluated.
if (showStatus && index > 0 && index === candlesCount - 1) {
    const statusText = seconds(time(0)) - seconds(time(1)) === 300 ? adaptive.status : 'Use 5m / wait after data gap';
    rectangle.create(time(1), high(1) + 10, time(0), high(1) + 6, {
      color: '#42a5f5', fillColor: 'rgba(66,165,245,0.12)',
      text: '2024 Adaptive: ' + statusText + ' | learned ' + adaptive.samples.length +
        ' | BUY ' + adaptive.buys + ' / SELL ' + adaptive.sells +
        ' | scores ' + adaptive.buyScore.toFixed(2) + '/' + adaptive.sellScore.toFixed(2) + 'R'
    });
}
// Price guides appear only while the simulated signal remains open.
plot(adaptive.active ? adaptive.active.leg.entry : NaN, { title: 'Entry', color: '#42a5f5' });
plot(adaptive.active ? adaptive.active.leg.stop : NaN, { title: 'Stop', color: '#ef5350' });
plot(adaptive.active ? adaptive.active.leg.target : NaN, { title: 'Target', color: '#00c878' });
