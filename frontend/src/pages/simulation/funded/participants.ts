// Port of Participants.luau and BotPopulation.Configure, preserving initialization draws.
import { INITIALIZATION, INSTRUMENTS, PLAYER, POPULATION, REGIMES, RESERVE, SCALPER, SETTINGS, SPECS, clamp, int, noise, random, range, round, type Direction, type Instrument, type Kind, type OrderRequest, type Participant, type State } from './model';
import { addAccount, advanceClock, cancelAll, cancelOrder, createExchange, exposureAllowed, processQueue, processStops, pruneBook, retryAttempted, retryReady, submitOrder, summary } from './exchange';

export function chooseRegime(s: State) {
  let draw = range(s, 0, REGIMES.reduce((n, r) => n + r.weight, 0)); let chosen = REGIMES[0];
  for (const regime of REGIMES) { draw -= regime.weight; if (draw <= 0) { chosen = regime; break; } }
  s.regime = chosen; s.direction = random(s) < .5 ? 1 : -1; s.nextRegime = s.time + range(s, 100, 240);
}
function agent(s: State, key: Instrument, kind: Kind, index: number) {
  const maker = kind === 'Maker', whale = kind === 'Whale', spec = SPECS[key];
  // Even overridden values consume their original source draws.
  const budget = int(s, maker ? 350000 : whale ? 600000 : 50000, maker ? 650000 : whale ? 1100000 : 160000);
  const a: Participant = {
    id: `bot:${key}:${kind}:${index}`, key, kind, budget, risk: range(s, .45, .85), limit: int(s, maker ? 140 : whale ? 180 : 16, maker ? 250 : whale ? 350 : 60),
    speed: range(s, maker ? 1.5 : whale ? 28 : 2.5, maker ? 4.5 : whale ? 100 : 13), size: int(s, maker ? 3 : whale ? 18 : 1, maker ? 9 : whale ? 60 : 5),
    bias: range(s, -.55, .55), belief: s.markets[key].fair + noise(s) * 9, patience: range(s, 4, 35), aggression: range(s, .2, .8), next: range(s, .2, 18), stop: int(s, 16, 85), target: int(s, 20, 140), parent: 0, parentSide: 1,
    lookback: 0, entryThreshold: 0, signalWeight: 0, valueOffset: 0, halfLife: 0, observationSeconds: 0, observationNoise: 0, observedAt: 0, nextObservation: 0, entryBuffer: 0, minMove: 0, quotes: {}, replenish: {}, maxHold: 0, cooldown: 0, style: index % 2 === 0 ? 'Momentum' : 'Reversion', readyAt: 0,
  };
  if (whale) a.bias = index % 2 === 0 ? -.3 : .3;
  if (kind === 'Scalper') {
    a.size = int(s, SCALPER.size[0], SCALPER.size[1]); a.limit = a.size;
    a.speed = range(s, SCALPER.decision[0], SCALPER.decision[1]); a.stop = int(s, SCALPER.stop[0], SCALPER.stop[1]); a.target = int(s, SCALPER.target[0], SCALPER.target[1]);
    a.maxHold = range(s, SCALPER.hold[0], SCALPER.hold[1]); a.patience = range(s, SCALPER.lifetime[0], SCALPER.lifetime[1]); a.cooldown = range(s, SCALPER.cooldown[0], SCALPER.cooldown[1]); a.lookback = int(s, 3, 12);
  }
  if (kind === 'Reserve') { a.budget = RESERVE.capital; a.risk = .8; a.limit = RESERVE.limit; a.size = RESERVE.sizes[key]; a.speed = RESERVE.decision; }
  a.budget = POPULATION[kind][1];
  const scale = clamp(12.5 / (spec.tick * spec.multiplier * spec.scale), .25, 1);
  if (kind !== 'Reserve') a.size = Math.max(1, Math.floor(a.size * scale));
  a.limit = Math.max(a.size, Math.floor(a.limit * scale));
  a.lookback = int(s, 8, 60); a.entryThreshold = range(s, .5, 1.2); a.signalWeight = range(s, .6, 1.5); a.valueOffset = noise(s) * 3 * spec.scale;
  a.halfLife = range(s, 6, 18); a.observationSeconds = range(s, 2, 8); a.observationNoise = range(s, 2, 6) * spec.scale;
  a.observedAt = s.time; a.nextObservation = s.time + range(s, .25, a.observationSeconds); a.entryBuffer = range(s, 2, 6); a.minMove = range(s, 3, 8);
  if (maker) a.speed *= SETTINGS.makerActivity;
  else if (whale) a.speed *= SETTINGS.whaleActivity;
  else if (kind === 'Reserve') a.limit = RESERVE.limit;
  else if (kind === 'Scalper') { a.limit = a.size; a.lookback = int(s, 8, 35); }
  else a.speed *= SETTINGS.traderActivity;
  a.limit = Math.min(a.limit, Math.floor(a.budget * a.risk * .5 / spec.margin)); a.size = Math.min(a.size, a.limit);
  addAccount(s, a.id, a.budget, a.limit, a.risk); s.participants.push(a);
}
export function initializeParticipants(s: State) {
  chooseRegime(s);
  for (const key of INSTRUMENTS) {
    const reference = s.books[key].reference; s.markets[key] = { fair: reference, anchor: reference, impulse: 0 };
    for (const kind of INITIALIZATION) for (let i = 1; i <= POPULATION[kind][0]; i++) agent(s, key, kind, i);
  }
  for (const a of s.participants) if (a.kind === 'Maker') quoteMaker(s, a);
  for (const a of s.participants) if (a.kind === 'Reserve') quoteReserve(s, a);
  for (const key of INSTRUMENTS) { const a = s.participants.find(a => a.key === key && a.kind !== 'Maker' && a.kind !== 'Reserve')!; submitOrder(s, a.id, { contract: key, direction: random(s) < .5 ? 1 : -1, quantity: 1, type: 'Market' }); }
}
export function fit(s: State, a: Participant, side: Direction, quantity: number) {
  const account = s.accounts[a.id]; let lo = 0, hi = Math.max(0, Math.floor(quantity));
  if (exposureAllowed(s, account, a.key, side, hi)) return hi;
  while (lo < hi) { const mid = Math.ceil((lo + hi) / 2); if (exposureAllowed(s, account, a.key, side, mid)) lo = mid; else hi = mid - 1; } return lo;
}
export function quoteMaker(s: State, a: Participant) {
  const b = s.books[a.key], account = s.accounts[a.id];
  if (account.locked || account.liquidating) { cancelAll(s, a.id, a.key); return; }
  const p = account.positions[a.key], inventory = p ? p.quantity * p.direction : 0;
  const stress = s.regime.volatility * SETTINGS.volatility + Math.min(6, b.volatility) * .2;
  const center = (b.last ?? b.reference) * .83 + s.markets[a.key].fair * .17 - inventory / a.limit * (5 + stress * 5);
  const spread = Math.max(1, Math.floor(stress * .8 + b.volatility * .2));
  let liquidity = s.regime.liquidity * SETTINGS.liquidity; if (s.time < s.newsUntil) liquidity *= .6;
  const activeDepth = liquidity < .35 ? 1 : liquidity < .7 ? 2 : 3;
  for (let d = 1; d <= 3; d++) for (const side of [1, -1] as const) {
    const slot = `${side}:${d}`; let held = s.orders[a.quotes[slot]];
    if (a.quotes[slot] && !held) { delete a.quotes[slot]; a.replenish[slot] = s.time + SETTINGS.makerReplenish; }
    const tick = Math.max(1, round(center - side * (spread + (d - 1) * Math.max(1, Math.floor(stress))))), eligible = d <= activeDepth;
    if (held && s.time - held.time >= SETTINGS.makerRest && (!eligible || Math.abs(held.price! - tick) >= SETTINGS.makerReprice)) { cancelOrder(s, a.id, held.id); delete a.quotes[slot]; held = s.orders[held.id]; }
    if (eligible && !held && s.time >= (a.replenish[slot] ?? 0)) {
      const size = fit(s, a, side, Math.max(1, Math.floor(a.size * SETTINGS.makerSize * liquidity * (1 + (d - 1) * .15) * (1 - inventory / a.limit * side * .8))));
      if (size > 0) { const result = submitOrder(s, a.id, { contract: a.key, direction: side, quantity: size, type: 'Limit', price: tick * SPECS[a.key].tick, expires: s.time + SETTINGS.makerRest * 6 }); if (result.ok && result.order.remaining) a.quotes[slot] = result.order.id; }
    }
  }
}
export function quoteReserve(s: State, a: Participant) {
  const b = s.books[a.key], account = s.accounts[a.id];
  if (account.locked || account.liquidating) { cancelAll(s, a.id, a.key); return; }
  const p = account.positions[a.key], inventory = p ? p.quantity * p.direction : 0, fair = round(s.markets[a.key].fair);
  b.fair = fair; const stress = Math.max(.25, Math.min(1, s.regime.liquidity)), skew = clamp(round(inventory / a.limit * 4), -4, 4);
  for (let d = 0; d < RESERVE.offsets.length; d++) for (const side of [1, -1] as const) {
    const slot = `${side}:${d + 1}`; let held = s.orders[a.quotes[slot]];
    const tick = Math.max(1, fair - skew - side * Math.ceil(RESERVE.offsets[d] * RESERVE.spacing));
    if (held && s.time - held.time >= RESERVE.rest && Math.abs(held.price! - tick) >= RESERVE.reprice) { cancelOrder(s, a.id, held.id); held = s.orders[held.id]; }
    if (!held) {
      delete a.quotes[slot]; const opposing = s.orders[(side === 1 ? b.asks : b.bids)[0]];
      if (!opposing || (tick - opposing.price!) * side < 0) {
        const variation = range(s, RESERVE.variation[0], RESERVE.variation[1]);
        const size = fit(s, a, side, Math.max(1, Math.floor(a.size * variation * stress * (1 + d * RESERVE.growth) * (1 - inventory / a.limit * side * .8))));
        if (size > 0) { const result = submitOrder(s, a.id, { contract: a.key, direction: side, quantity: size, type: 'Limit', price: tick * SPECS[a.key].tick, expires: s.time + RESERVE.rest * 30 }); if (result.ok && result.order.remaining) a.quotes[slot] = result.order.id; }
      }
    }
  }
}
export function observeValue(s: State, a: Participant) {
  if (a.kind !== 'Value' || s.time < a.nextObservation) return;
  const elapsed = Math.max(0, s.time - a.observedAt), observation = s.markets[a.key].fair + a.valueOffset + noise(s) * a.observationNoise;
  a.belief += (observation - a.belief) * (1 - Math.exp(-Math.log(2) * elapsed / a.halfLife));
  a.observedAt = s.time; a.nextObservation = s.time + a.observationSeconds * range(s, .7, 1.3);
}
function repairRisk(s: State, a: Participant) {
  const account = s.accounts[a.id]; let needs = false;
  for (const key of INSTRUMENTS) {
    const p = account.positions[key]; if (!p) continue;
    if (p.triggered) { needs = true; continue; }
    const maximum = Math.min(account.limit, Math.max(0, Math.floor(summary(s, account).equity * account.risk / SPECS[key].margin)));
    if (p.quantity > maximum) {
      needs = true; cancelAll(s, a.id, key);
      if (retryReady(s, account, key, p)) { const result = submitOrder(s, a.id, { contract: key, direction: -p.direction as Direction, quantity: Math.min(64, p.quantity - maximum), type: 'Market', reduceOnly: true }); if (!result.ok || !result.order.filled) retryAttempted(s, key, p); }
    }
  }
  return needs;
}
export function tradeScalper(s: State, a: Participant) {
  const account = s.accounts[a.id], b = s.books[a.key], p = account.positions[a.key];
  if (p) {
    if (a.positionId !== p.id) { a.positionId = p.id; a.openedAt = s.time; cancelAll(s, a.id, a.key); }
    if (p.triggered || account.liquidating) { cancelAll(s, a.id, a.key); return; }
    if (s.time - a.openedAt! >= a.maxHold || account.locked) {
      cancelAll(s, a.id, a.key);
      if (retryReady(s, account, a.key, p)) { const result = submitOrder(s, a.id, { contract: a.key, direction: -p.direction as Direction, quantity: p.quantity, type: 'Market', reduceOnly: true }); if (!result.ok || !result.order.filled) retryAttempted(s, a.key, p); } return;
    }
    if (!account.openOrders.length) submitOrder(s, a.id, { contract: a.key, direction: -p.direction as Direction, quantity: p.quantity, type: 'Limit', price: Math.max(1, round(p.entry / SPECS[a.key].tick) + p.direction * a.target) * SPECS[a.key].tick, reduceOnly: true });
    return;
  }
  if (a.positionId !== undefined) { cancelAll(s, a.id, a.key); delete a.positionId; delete a.openedAt; a.readyAt = s.time + a.cooldown * range(s, .75, 1.25); }
  if (account.locked || account.liquidating) { cancelAll(s, a.id, a.key); return; }
  if (s.time < a.readyAt || account.openOrders.length) return;
  pruneBook(s, a.key);
  const bid = s.orders[b.bids[0]], ask = s.orders[b.asks[0]]; if (!bid || !ask) return;
  const spread = ask.price! - bid.price!; if (spread < 1 || spread > SCALPER.spread || spread >= a.target) return;
  const last = b.last ?? b.reference, previous = b.recent[Math.max(0, b.recent.length - a.lookback - 1)] ?? last;
  const move = (last - previous) / Math.max(1, b.volatility * 2), signal = a.bias * .2 + noise(s) * .2 + (a.style === 'Momentum' ? move : -move);
  if (Math.abs(last - previous) < Math.max(a.minMove, spread + SCALPER.slippage + a.entryBuffer) || Math.abs(signal) < a.entryThreshold) return;
  const side = signal > 0 ? 1 : -1, aggressive = random(s) < SCALPER.aggressive;
  const tick = aggressive ? side === 1 ? ask.price! + SCALPER.slippage : bid.price! - SCALPER.slippage : side === 1 ? bid.price! : ask.price!;
  const size = fit(s, a, side, a.size); if (size < 1) return;
  const result = submitOrder(s, a.id, { contract: a.key, direction: side, quantity: size, type: 'Limit', price: Math.max(1, tick) * SPECS[a.key].tick, stopTicks: a.stop, expires: s.time + a.patience });
  if (result.ok) { a.readyAt = s.time + a.cooldown; if (result.order.filled > 0) { cancelAll(s, a.id, a.key); const held = account.positions[a.key]; if (held) { a.positionId = held.id; a.openedAt = s.time; } } }
}
export function tradeParticipant(s: State, a: Participant) {
  const b = s.books[a.key], account = s.accounts[a.id], spec = SPECS[a.key];
  if (account.locked || account.liquidating || account.positions[a.key]?.triggered || random(s) < .25) return;
  const last = b.last ?? b.reference;
  if (a.kind !== 'Value') a.belief += (s.markets[a.key].fair - a.belief) * range(s, .02, .18) + noise(s) * s.regime.volatility * spec.scale * .6;
  const p = account.positions[a.key], inventory = p ? p.quantity * p.direction : 0;
  const previous = b.recent[Math.max(0, b.recent.length - a.lookback - 1)] ?? last;
  const momentum = (last - previous) / Math.max(3, b.volatility * 8), edge = (a.belief - last) / Math.max(4, b.volatility * 8);
  let signal = a.bias + noise(s) * 1.3 - inventory / a.limit * 1.8;
  if (a.kind === 'Momentum') signal += momentum * range(s, .5, 1.4);
  else if (a.kind === 'Value') {
    const bid = s.orders[b.bids[0]], ask = s.orders[b.asks[0]]; if (!bid || !ask || Math.abs(a.belief - last) < (ask.price! - bid.price!) / 2 + a.entryBuffer) return;
    signal = edge * a.signalWeight + a.bias * .2 + noise(s) * .2 - inventory / a.limit * 1.8;
  } else if (a.kind === 'Reversion') { signal -= momentum * range(s, .6, 1.6); signal += edge * .3; }
  else if (a.kind === 'Protective') signal += edge * .4;
  if (Math.abs(signal) < a.entryThreshold) return;
  let side: Direction = signal > 0 ? 1 : -1, size = int(s, 1, a.size), aggressive = random(s) < a.aggression;
  if (a.kind === 'Whale') {
    if (a.parent > 0) { side = a.parentSide; size = Math.min(a.parent, int(s, 4, Math.max(5, Math.floor(a.size / 3)))); a.parent -= size; }
    else { size = int(s, Math.floor(a.size / 2), a.size); if (random(s) < .55) { a.parent = size * int(s, 2, 4); a.parentSide = side; size = Math.min(a.parent, int(s, 4, 12)); a.parent -= size; } }
    aggressive = random(s) < .8;
  }
  if (random(s) < .45) for (const id of [...account.openOrders].sort((a, b) => a - b)) { const o = s.orders[id]; if (o && s.time - o.time >= Math.max(12, a.patience)) cancelOrder(s, a.id, id); }
  size = fit(s, a, side, size); if (size < 1) return;
  const request: OrderRequest = { contract: a.key, direction: side, quantity: size, type: aggressive ? 'Market' : 'Limit', expires: s.time + a.patience * range(s, .5, 1.5) };
  if (!aggressive) { const distance = int(s, 1, Math.max(2, Math.floor(3 + s.regime.volatility * 3))); request.price = Math.max(1, round(last - side * distance)) * spec.tick; }
  if (a.kind === 'Protective' || a.kind !== 'Maker' && random(s) < .16) { request.stopTicks = a.stop; request.targetTicks = a.target; }
  submitOrder(s, a.id, request);
}
export function stepSimulation(s: State) {
  advanceClock(s);
  if (s.time >= s.nextRegime) chooseRegime(s);
  if (s.time >= s.nextNews) {
    const sign = random(s) < .5 ? 1 : -1, magnitude = range(s, 8, 40) * SETTINGS.volatility;
    for (const key of INSTRUMENTS) { const m = s.markets[key], shock = sign * magnitude * SPECS[key].scale * range(s, .4, 1.5); m.anchor += shock; m.impulse += shock * .1; }
    s.news = `${sign > 0 ? 'Optimistic' : 'Cautious'} synthetic outlook`; s.newsUntil = s.time + range(s, 20, 55); s.nextNews = s.time + range(s, 140, 420) / SETTINGS.newsFrequency;
  }
  if (s.time >= s.nextBeliefs) {
    for (const key of INSTRUMENTS) {
      const m = s.markets[key], spec = SPECS[key];
      m.anchor += s.regime.trend * s.direction * spec.scale + noise(s) * s.regime.volatility * SETTINGS.volatility * spec.scale * .16;
      m.fair += (m.anchor - m.fair) * .025 + m.impulse * .06; m.impulse *= .96;
      m.fair = clamp(m.fair, s.books[key].reference * .6, s.books[key].reference * 1.4); s.books[key].fair = round(m.fair);
    }
    s.nextBeliefs = s.time + 1;
  }
  for (const a of s.participants) {
    observeValue(s, a);
    if (s.time < a.next) continue;
    const repairing = repairRisk(s, a);
    if (!repairing) { if (a.kind === 'Maker') quoteMaker(s, a); else if (a.kind === 'Reserve') quoteReserve(s, a); else if (a.kind === 'Scalper') tradeScalper(s, a); else tradeParticipant(s, a); }
    let delay = a.speed * range(s, .45, 1.65) / s.regime.activity;
    if (a.kind === 'Maker') delay *= SETTINGS.makerDecision;
    if (a.kind === 'Whale') delay = a.parent > 0 ? range(s, 1.5, 6) * SETTINGS.whaleActivity : delay / SETTINGS.whaleFrequency;
    if (repairing) delay = range(s, 2, 5);
    a.next = s.time + delay; processStops(s);
  }
  processQueue(s);
}
export function createSimulation(seed = 0, warmupSteps = SETTINGS.warmup / SETTINGS.step) {
  const s = createExchange(seed); initializeParticipants(s);
  for (let i = 0; i < warmupSteps; i++) stepSimulation(s);
  addAccount(s, PLAYER, 10000, 10, .8, true); return s;
}
