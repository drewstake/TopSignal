// Port of Participants.luau. Keep every random draw and loop in source order.
import { addAccount, advanceClock, cancelAll, processStops, submitOrder } from './simulationExchange';
import { initializeStructureParticipants, isStructural, scheduleStructure, tradeStructure, updateStructure } from './simulationStructure';
import { BEHAVIORS, clamp, int, noise, random, range, round,
  type Direction, type Instrument, type OrderRequest, type Participant, type ParticipantKind, type SimulationState } from './simulationModel';

function addEvent(s: SimulationState, kind: 'Regime' | 'News', name: string, direction: Direction) {
  s.events.push({ step: s.step, kind, name, direction });
  if (s.events.length > 40) s.events.shift();
}
export function chooseRegime(s: SimulationState) {
  let draw = range(s, 0, s.config.regimes.reduce((total, r) => total + r.weight, 0));
  let chosen = s.config.regimes[0];
  for (const regime of s.config.regimes) { draw -= regime.weight; if (draw <= 0) { chosen = regime; break; } }
  s.regime = chosen; s.direction = random(s) < 0.5 ? 1 : -1;
  s.nextRegime = s.time + range(s, s.config.regimeSeconds[0], s.config.regimeSeconds[1]);
  addEvent(s, 'Regime', chosen.name, s.direction);
}
function agent(s: SimulationState, key: Instrument, kind: ParticipantKind, index: number) {
  const maker = kind === 'Maker', whale = kind === 'Whale';
  const id = `bot:${key}:${kind}:${index}`;
  const budget = int(s, maker ? 350000 : whale ? 600000 : 50000, maker ? 650000 : whale ? 1100000 : 160000);
  const a: Participant = {
    id, key, kind, budget, risk: range(s, 0.45, 0.85), limit: int(s, maker ? 140 : whale ? 180 : 16, maker ? 250 : whale ? 350 : 60),
    speed: range(s, maker ? 1.5 : whale ? 28 : 2.5, maker ? 4.5 : whale ? 100 : 13),
    size: int(s, maker ? 3 : whale ? 18 : 1, maker ? 9 : whale ? 60 : 5),
    bias: range(s, -0.55, 0.55), belief: s.markets[key].fair + noise(s) * 9,
    patience: range(s, 4, 35), aggression: range(s, 0.2, 0.8), next: range(s, 0.2, 18),
    stop: int(s, 16, 85), target: int(s, 20, 140), parent: 0, parentSide: 1,
  };
  if (whale) a.bias = index % 2 === 0 ? -0.3 : 0.3;
  addAccount(s, id, budget, a.limit, a.risk); s.participants.push(a);
}
export function initializeParticipants(s: SimulationState) {
  chooseRegime(s);
  for (const key of s.config.contractOrder) {
    s.markets[key] = { fair: s.books[key].reference, anchor: s.books[key].reference, impulse: 0 };
    for (let i = 1; i <= s.config.makers; i++) agent(s, key, 'Maker', i);
    for (let i = 1; i <= s.config.traders; i++) {
      let draw = range(s, 0, BEHAVIORS.reduce((total, kind) => total + s.config.weights[kind], 0));
      let chosen: ParticipantKind = 'Liquidity';
      for (const kind of BEHAVIORS) { draw -= s.config.weights[kind]; if (draw <= 0) { chosen = kind; break; } }
      agent(s, key, chosen, i);
    }
    if (s.config.whaleFrequency > 0) for (let i = 1; i <= s.config.whales; i++) agent(s, key, 'Whale', i);
  }
  for (const a of s.participants) if (a.kind === 'Maker') quoteMaker(s, a);
  for (const key of s.config.contractOrder) {
    const a = s.participants.find(p => p.key === key && p.kind !== 'Maker');
    if (a) submitOrder(s, a.id, { contract: key, direction: random(s) < 0.5 ? 1 : -1, quantity: 1, type: 'Market' });
  }
  initializeStructureParticipants(s);
}
export function quoteMaker(s: SimulationState, a: Participant) {
  const b = s.books[a.key], c = s.config.contracts[a.key], market = s.markets[a.key];
  cancelAll(s, a.id, a.key);
  const account = s.accounts[a.id]; if (account.locked) return;
  const p = account.positions[a.key], inventory = p ? p.quantity * p.direction : 0;
  const stress = s.regime.volatility * s.config.volatility + Math.min(6, b.volatility) * 0.2;
  const center = (b.last ?? b.reference) * 0.83 + market.fair * 0.17 - inventory / a.limit * (5 + stress * 5) + noise(s) * 0.7;
  const spread = Math.max(1, Math.floor(stress * 0.8 + b.volatility * 0.2 + range(s, 0, 1.4)));
  let liquidity = s.regime.liquidity * s.config.liquidity;
  if (s.time < s.newsUntil) liquidity *= 0.6;
  if (random(s) > Math.min(1, liquidity * 1.7)) return;
  for (let depth = 1; depth <= 3; depth++) for (const side of [1, -1] as const) {
    const size = Math.max(1, Math.floor(a.size * liquidity * range(s, 0.55, 1.25) * (1 - inventory / a.limit * side * 0.8)));
    const tick = Math.max(1, round(center - side * (spread + (depth - 1) * Math.max(1, Math.floor(stress)))));
    submitOrder(s, a.id, { contract: a.key, direction: side, quantity: size, type: 'Limit', price: tick * c.tick, expires: s.time + a.speed * 2.8 });
  }
}
export function tradeParticipant(s: SimulationState, a: Participant) {
  if (isStructural(a)) { tradeStructure(s, a); return; }
  const b = s.books[a.key], c = s.config.contracts[a.key], account = s.accounts[a.id];
  if (account.locked) return;
  if (random(s) < 0.25) return;
  const last = b.last ?? b.reference, market = s.markets[a.key];
  a.belief += (market.fair - a.belief) * range(s, 0.02, 0.18) + noise(s) * s.regime.volatility * c.scale * 0.6;
  const p = account.positions[a.key], inventory = p ? p.quantity * p.direction : 0;
  // Lua arrays start at one; #recent - lookback maps to length - lookback - 1.
  const previous = b.recent[Math.max(0, b.recent.length - int(s, 8, 35) - 1)] ?? last;
  const momentum = (last - previous) / Math.max(3, b.volatility * 8);
  const edge = (a.belief - last) / Math.max(4, b.volatility * 8);
  let signal = a.bias + noise(s) * 1.3 - inventory / a.limit * 1.8;
  if (a.kind === 'Momentum') signal += momentum * range(s, 0.5, 1.4);
  else if (a.kind === 'Value') signal += edge * range(s, 0.6, 1.5);
  else if (a.kind === 'Reversion') { signal -= momentum * range(s, 0.6, 1.6); signal += edge * 0.3; }
  else if (a.kind === 'Protective') signal += edge * 0.4;
  if (Math.abs(signal) < range(s, 0.1, 0.55)) return;
  let side: Direction = signal > 0 ? 1 : -1, size = int(s, 1, a.size);
  let aggressive = random(s) < a.aggression;
  if (a.kind === 'Whale') {
    if (a.parent > 0) { side = a.parentSide; size = Math.min(a.parent, int(s, 4, Math.max(5, Math.floor(a.size / 3)))); a.parent -= size; }
    else {
      size = int(s, Math.floor(a.size / 2), a.size);
      if (random(s) < 0.55) { a.parent = size * int(s, 2, 4); a.parentSide = side; size = Math.min(a.parent, int(s, 4, 12)); a.parent -= size; }
    }
    aggressive = random(s) < 0.8;
  }
  if (random(s) < 0.45) cancelAll(s, a.id, a.key);
  const request: OrderRequest = { contract: a.key, direction: side, quantity: size, type: aggressive ? 'Market' : 'Limit', expires: s.time + a.patience * range(s, 0.5, 1.5) };
  if (!aggressive) {
    const distance = int(s, 1, Math.max(2, Math.floor(3 + s.regime.volatility * 3)));
    request.price = Math.max(1, round(last - side * distance)) * c.tick;
  }
  if (a.kind === 'Protective' || (a.kind !== 'Maker' && random(s) < 0.16)) { request.stopTicks = a.stop; request.targetTicks = a.target; }
  submitOrder(s, a.id, request);
}
function updateStructures(s: SimulationState) {
  for (const a of s.participants) if (isStructural(a)) updateStructure(s, a);
}
export function stepParticipants(s: SimulationState) {
  advanceClock(s, updateStructures);
  if (s.time >= s.nextRegime) chooseRegime(s);
  if (s.config.newsFrequency > 0 && s.time >= s.nextNews) {
    const sign = random(s) < 0.5 ? 1 : -1;
    const magnitude = range(s, 8, 40) * s.config.volatility;
    for (const key of s.config.contractOrder) {
      const m = s.markets[key], shock = sign * magnitude * s.config.contracts[key].scale * range(s, 0.4, 1.5);
      m.anchor += shock; m.impulse += shock * 0.1;
    }
    s.news = (sign > 0 ? 'Optimistic' : 'Cautious') + ' synthetic outlook';
    s.newsUntil = s.time + range(s, 20, 55); s.nextNews = s.time + range(s, 140, 420) / s.config.newsFrequency;
    addEvent(s, 'News', s.news, sign);
  }
  if (s.time >= s.nextBeliefs) {
    for (const key of s.config.contractOrder) {
      const m = s.markets[key], c = s.config.contracts[key];
      m.anchor += s.regime.trend * s.direction * c.scale + noise(s) * s.regime.volatility * s.config.volatility * c.scale * 0.16;
      m.fair += (m.anchor - m.fair) * 0.025 + m.impulse * 0.06;
      m.impulse *= 0.96;
      m.fair = clamp(m.fair, s.books[key].reference * 0.6, s.books[key].reference * 1.4);
    }
    s.nextBeliefs = s.time + 1;
  }
  for (const a of s.participants) if (s.time >= a.next) {
    if (isStructural(a)) { tradeStructure(s, a); scheduleStructure(s, a); processStops(s); continue; }
    if (a.kind === 'Maker') quoteMaker(s, a); else tradeParticipant(s, a);
    let delay = a.speed * range(s, 0.45, 1.65) / s.regime.activity;
    if (a.kind === 'Whale') delay = a.parent > 0 ? range(s, 1.5, 6) : delay / Math.max(0.0001, s.config.whaleFrequency);
    a.next = s.time + delay;
    processStops(s);
  }
}
