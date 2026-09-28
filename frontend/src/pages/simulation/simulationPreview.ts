// A minimal balanced market, independent of accounts, broker feeds, and Bot.
export const UPDATES_PER_SECOND = 60;
export const UPDATE_INTERVAL_MS = 1000 / UPDATES_PER_SECOND;
export const UPDATES_PER_CANDLE = 5 * UPDATES_PER_SECOND;

export interface SimulationCandle {
  update: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// Participants place resting orders at individually sampled offsets.
// Here 1% is the maximum offset, not a statistical variance or fixed spread.
const STARTING_PRICE = 5;
const MAX_OFFSET = 0.01;
// Illustrative sizes for this experiment, not calibrated market populations.
export const PARTICIPANT_GROUPS = [
  { kind: "retail", label: "Retail", count: 1000, orderSize: 1, twoSided: false },
  { kind: "whale", label: "Whales", count: 60, orderSize: 1000, twoSided: false },
  { kind: "institution", label: "Institutions", count: 100, orderSize: 250, twoSided: false },
  { kind: "market-maker", label: "Market makers", count: 40, orderSize: 100, twoSided: true },
] as const;

export type ParticipantKind = typeof PARTICIPANT_GROUPS[number]["kind"];
export interface MarketParticipant {
  id: string;
  kind: ParticipantKind;
  sides: readonly ("buy" | "sell")[];
  orderSize: number;
}

export const MARKET_PARTICIPANTS: readonly MarketParticipant[] = PARTICIPANT_GROUPS.flatMap((group) =>
  Array.from({ length: group.count }, (_, index): MarketParticipant => ({
    id: `${group.kind}-${index + 1}`,
    kind: group.kind,
    sides: group.twoSided ? ["buy", "sell"] : [index < group.count / 2 ? "buy" : "sell"],
    orderSize: group.orderSize,
  })));

export interface ParticipantOrder {
  id: string;
  participantId: string;
  kind: ParticipantKind;
  side: "buy" | "sell";
  price: number;
  quantity: number;
}

function quoteBounds(value: number) {
  const cents = Math.round(value * 100);
  return {
    low: Math.max(1, Math.ceil(cents * (1 - MAX_OFFSET) - 1e-9)),
    high: Math.max(1, Math.floor(cents * (1 + MAX_OFFSET) + 1e-9)),
  };
}

// Uniformly sample all valid one-cent prices in the full -1% to +1% range.
export function sampleQuotePrice(value: number, random: number): number {
  const { low, high } = quoteBounds(value);
  return (low + Math.floor(random * (high - low + 1))) / 100;
}

function makerPrices(value: number, random: () => number) {
  const first = sampleQuotePrice(value, random());
  const second = sampleQuotePrice(value, random());
  let bid = Math.min(first, second);
  let ask = Math.max(first, second);
  const { low, high } = quoteBounds(value);
  if (bid === ask && high > low) {
    if (Math.round(ask * 100) < high) ask = (Math.round(ask * 100) + 1) / 100;
    else bid = (Math.round(bid * 100) - 1) / 100;
  }
  // At sub-dollar prices there may be only one valid cent tick. Quote only one
  // side in that case instead of crossing the maker's own bid and ask.
  return { bid, ask: bid < ask ? ask : null };
}

function createParticipantOrders(): ParticipantOrder[] {
  let seed = 42;
  const random = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  // Large participants enter gradually through execution goals, not startup walls.
  return MARKET_PARTICIPANTS.filter((p) => p.kind !== "whale" && p.kind !== "institution").flatMap((participant) => {
    const prices = participant.kind === "market-maker" ? makerPrices(STARTING_PRICE, random) : null;
    return participant.sides.flatMap((side): ParticipantOrder[] => {
      const price = prices ? (side === "buy" ? prices.bid : prices.ask) : sampleQuotePrice(STARTING_PRICE, random());
      return price === null ? [] : [{
        id: `${participant.id}-${side}`, participantId: participant.id, kind: participant.kind,
        side, price, quantity: participant.orderSize,
      }];
    });
  });
}

export const PARTICIPANT_ORDERS: readonly ParticipantOrder[] = createParticipantOrders();
export const summarizeParticipants = (book: readonly ParticipantOrder[]) => PARTICIPANT_GROUPS.map((group) => {
  const participants = MARKET_PARTICIPANTS.filter((participant) => participant.kind === group.kind);
  const orders = book.filter((order) => order.kind === group.kind && order.quantity > 0);
  return {
    ...group,
    count: participants.length,
    buyers: orders.filter((order) => order.side === "buy").length,
    sellers: orders.filter((order) => order.side === "sell").length,
    bidShares: orders.filter((order) => order.side === "buy").reduce((sum, order) => sum + order.quantity, 0),
    askShares: orders.filter((order) => order.side === "sell").reduce((sum, order) => sum + order.quantity, 0),
  };
});
export const MARKET_RETAIL_PARTICIPANTS = Array.from({ length: 2000 }, (_, index) => ({
  id: `market-retail-${index + 1}`,
}));

export interface RetailActivity {
  mode: "Quiet" | "Active" | "Burst";
  ordersPerSecond: number;
  nextChangeUpdate: number;
}

export function sampleRetailActivity(update: number, random: () => number): RetailActivity {
  const roll = random();
  const [mode, minimum, maximum] = roll < 0.3
    ? ["Quiet", 40, 100] as const
    : roll < 0.8 ? ["Active", 120, 240] as const : ["Burst", 300, 500] as const;
  return {
    mode, ordersPerSecond: minimum + Math.floor(random() * (maximum - minimum + 1)),
    nextChangeUpdate: update + Math.round((2 + random() * 4) * UPDATES_PER_SECOND),
  };
}

export function sampleRetailOrderSize(random: () => number): number {
  const roll = random();
  // 90% small orders, 9% medium orders, 1% occasional larger orders.
  const [minimum, maximum] = roll < 0.9 ? [1, 3] : roll < 0.99 ? [4, 10] : [11, 40];
  return minimum + Math.floor(random() * (maximum - minimum + 1));
}

export function sampleRetailArrivals(ordersPerSecond: number, random: () => number): number {
  // Poisson arrivals: some frames have no orders, others several. The activity
  // rate is an expectation, not a fixed quota or a cap on a particular second.
  const threshold = Math.exp(-ordersPerSecond / UPDATES_PER_SECOND);
  let product = random();
  let orders = 0;
  while (product > threshold) {
    orders += 1;
    product *= random();
  }
  return orders;
}

export function summarizeBook(book: readonly ParticipantOrder[], price: number) {
  const buys = book.filter((order) => order.side === "buy" && order.quantity > 0);
  const sells = book.filter((order) => order.side === "sell" && order.quantity > 0);
  return {
    price,
    bid: buys.length ? Math.max(...buys.map((order) => order.price)) : null,
    lowestBid: buys.length ? Math.min(...buys.map((order) => order.price)) : null,
    ask: sells.length ? Math.min(...sells.map((order) => order.price)) : null,
    highestAsk: sells.length ? Math.max(...sells.map((order) => order.price)) : null,
    buyers: buys.length,
    sellers: sells.length,
    bidShares: buys.reduce((sum, order) => sum + order.quantity, 0),
    askShares: sells.reduce((sum, order) => sum + order.quantity, 0),
  };
}

export interface SimulationFill {
  takerId: string;
  makerId: string;
  side: "buy" | "sell";
  price: number;
  quantity: number;
}

// Match at the resting quote's price, best price first, then original order
// sequence for ties. Unfilled quantity never creates a trade or moves the price.
export function executeMarketOrder(book: readonly ParticipantOrder[], takerId: string, side: "buy" | "sell", quantity: number, limitPrice?: number) {
  const nextBook = book.map((order) => ({ ...order }));
  const available = nextBook.filter((order) => order.side !== side && order.quantity > 0 && order.participantId !== takerId
    && (limitPrice === undefined || (side === "buy" ? order.price <= limitPrice : order.price >= limitPrice)))
    .sort((a, b) => side === "buy" ? a.price - b.price : b.price - a.price);
  const fills: SimulationFill[] = [];
  let remaining = quantity;
  for (const order of available) {
    if (remaining <= 0) break;
    const filled = Math.min(remaining, order.quantity);
    order.quantity -= filled;
    remaining -= filled;
    fills.push({ takerId, makerId: order.participantId, side, price: order.price, quantity: filled });
  }
  return { book: nextBook, fills, unfilled: remaining };
}

export function submitLimitOrder(book: readonly ParticipantOrder[], incoming: ParticipantOrder) {
  const result = executeMarketOrder(book.filter((order) => order.id !== incoming.id), incoming.participantId,
    incoming.side, incoming.quantity, incoming.price);
  // Keep the exhausted order record at zero so participant accounting stays bounded.
  return { book: [...result.book, { ...incoming, quantity: result.unfilled }], fills: result.fills };
}

const MARKET_MAKERS = MARKET_PARTICIPANTS.filter((p) => p.kind === "market-maker");

// Each maker samples a fresh pair anywhere within +/-1% of the current value.
// Its lower sample is the bid and upper sample is the ask, avoiding self-crosses.
// Quotes crossing another participant execute at that participant's resting price.
export function repriceMarketMakers(book: readonly ParticipantOrder[], lastPrice: number, random: () => number, participants: readonly MarketParticipant[] = MARKET_MAKERS) {
  const replacing = new Set(participants.map((participant) => participant.id));
  let nextBook = book.filter((order) => !replacing.has(order.participantId));
  const fills: SimulationFill[] = [];
  for (const participant of participants) {
    const existing = book.filter((order) => order.participantId === participant.id);
    if (!existing.length) continue;
    const prices = makerPrices(lastPrice, random);
    for (const side of participant.sides) {
      const price = side === "buy" ? prices.bid : prices.ask;
      if (price === null) continue;
      const previous = existing.find((order) => order.side === side);
      const result = submitLimitOrder(nextBook, {
        id: `${participant.id}-${side}`, participantId: participant.id, kind: participant.kind,
        side, price, quantity: previous && previous.quantity > 0 ? previous.quantity : participant.orderSize,
      });
      nextBook = result.book;
      fills.push(...result.fills);
    }
  }
  return { book: nextBook, fills };
}

export interface ParticipantGoal {
  participantId: string;
  kind: "whale" | "institution";
  side: "buy" | "sell";
  target: number;
  filled: number;
  nextActionUpdate: number;
  completedGoals: number;
  totalFilled: number;
  style: "Patient" | "Balanced" | "Urgent";
  intervalUpdates: number;
  pauseUpdates: number;
  priceTolerance: number;
}

export function sampleParticipantGoal(participantId: string, kind: ParticipantGoal["kind"], update: number, random: () => number): ParticipantGoal {
  const roll = random();
  const style = roll < 0.35 ? "Patient" : roll < 0.75 ? "Balanced" : "Urgent";
  const [minimum, maximum] = kind === "whale" ? [500, 2000] : [100, 600];
  const [waitLow, waitHigh] = style === "Patient" ? [6, 14] : style === "Balanced" ? [2, 6] : [0.3, 1.5];
  const [toleranceLow, toleranceHigh] = style === "Patient" ? [0.001, 0.003] : style === "Balanced" ? [0.002, 0.007] : [0.005, 0.01];
  return {
    participantId, kind, style, side: random() < 0.5 ? "buy" : "sell",
    target: minimum + Math.floor(random() * (maximum - minimum + 1)), filled: 0,
    intervalUpdates: Math.round((waitLow + random() * (waitHigh - waitLow)) * UPDATES_PER_SECOND),
    pauseUpdates: Math.round((2 + random() * 13) * UPDATES_PER_SECOND),
    priceTolerance: toleranceLow + random() * (toleranceHigh - toleranceLow),
    nextActionUpdate: update + 1 + Math.floor(random() * 10 * UPDATES_PER_SECOND),
    completedGoals: 0, totalFilled: 0,
  };
}

// Credit both aggressive executions and fills of an existing resting slice.
export function recordGoalFills(goals: readonly ParticipantGoal[], fills: readonly SimulationFill[], update: number): ParticipantGoal[] {
  return goals.map((goal) => {
    const executed = fills.reduce((sum, fill) => sum + (
      (fill.takerId === goal.participantId && fill.side === goal.side)
      || (fill.makerId === goal.participantId && fill.side !== goal.side) ? fill.quantity : 0), 0);
    if (!executed) return goal;
    const filled = goal.filled + executed;
    const completed = goal.filled < goal.target && filled === goal.target;
    return { ...goal, filled, totalFilled: goal.totalFilled + executed,
      completedGoals: goal.completedGoals + Number(completed),
      nextActionUpdate: completed ? update + goal.pauseUpdates : goal.nextActionUpdate };
  });
}

export function advanceParticipantGoals(book: readonly ParticipantOrder[], goals: readonly ParticipantGoal[], lastPrice: number, update: number, random: () => number) {
  let nextBook = [...book];
  let nextGoals = [...goals];
  const fills: SimulationFill[] = [];
  for (let index = 0; index < nextGoals.length; index += 1) {
    let goal = nextGoals[index];
    if (update < goal.nextActionUpdate) continue;
    // Cancel the previous slice before submitting another, so committed shares
    // never exceed the unfilled goal. Reuse one order ID to keep storage bounded.
    nextBook = nextBook.filter((order) => order.participantId !== goal.participantId);
    if (goal.filled === goal.target) {
      goal = { ...sampleParticipantGoal(goal.participantId, goal.kind, update, random),
        completedGoals: goal.completedGoals, totalFilled: goal.totalFilled };
    }
    const [sliceLow, sliceHigh] = goal.style === "Patient" ? [0.02, 0.06] : goal.style === "Balanced" ? [0.05, 0.15] : [0.1, 0.3];
    const remaining = goal.target - goal.filled;
    const slice = Math.max(1, Math.round(goal.target * (sliceLow + random() * (sliceHigh - sliceLow))));
    const quantity = goal.style === "Urgent" && remaining <= goal.target * 0.2 ? remaining : Math.min(remaining, slice);
    nextGoals[index] = { ...goal, nextActionUpdate: update + Math.max(1, Math.round(goal.intervalUpdates * (0.75 + random() * 0.5))) };
    const reference = fills.at(-1)?.price ?? lastPrice;
    const direction = goal.side === "buy" ? 1 : -1;
    // Each trader has its own maximum concession from the current reference.
    // Even an aggressive order stops at this price instead of sweeping blindly.
    const rawLimit = reference * (1 + direction * goal.priceTolerance) * 100;
    const limit = Math.max(1, goal.side === "buy" ? Math.floor(rawLimit + 1e-9) : Math.ceil(rawLimit - 1e-9)) / 100;
    const aggressive = random() < (goal.style === "Patient" ? 0.05 : goal.style === "Balanced" ? 0.5 : 0.9);
    let price = limit;
    if (!aggressive) {
      const offset = goal.style === "Patient" ? -random() * goal.priceTolerance
        : (random() * 2 - 1) * goal.priceTolerance;
      price = Math.max(1, Math.round(reference * (1 + direction * offset) * 100)) / 100;
      price = goal.side === "buy" ? Math.min(price, limit) : Math.max(price, limit);
      if (goal.style === "Patient") {
        // Patient limits remain passive and retain queue position until their
        // next scheduled decision or a fill, even while other traders act.
        const opposing = nextBook.filter((order) => order.side !== goal.side && order.quantity > 0);
        if (opposing.length) {
          price = goal.side === "buy"
            ? Math.min(price, Math.round(Math.min(...opposing.map((order) => order.price)) * 100 - 1) / 100)
            : Math.max(price, Math.round(Math.max(...opposing.map((order) => order.price)) * 100 + 1) / 100);
        }
      }
    }
    // At the one-cent floor a passive bid below the ask may not be possible.
    if (price < 0.01) continue;
    const result = aggressive
      ? executeMarketOrder(nextBook, goal.participantId, goal.side, quantity, limit)
      : submitLimitOrder(nextBook, {
        id: `${goal.participantId}-goal`, participantId: goal.participantId, kind: goal.kind,
        side: goal.side, quantity, price,
      });
    nextBook = result.book;
    fills.push(...result.fills);
    // Update immediately: this slice may also fill another participant's goal.
    nextGoals = recordGoalFills(nextGoals, result.fills, update);
  }
  return { book: nextBook, goals: nextGoals, fills };
}

function candleFromFills(update: number, fills: SimulationFill[], previousClose?: number): SimulationCandle | null {
  return fills.length ? {
    update, open: previousClose ?? fills[0].price, high: Math.max(previousClose ?? fills[0].price, ...fills.map((fill) => fill.price)),
    low: Math.min(previousClose ?? fills[0].price, ...fills.map((fill) => fill.price)), close: fills[fills.length - 1].price,
    volume: fills.reduce((sum, fill) => sum + fill.quantity, 0),
  } : null;
}

export interface SimulationState {
  update: number;
  seed: number;
  book: ParticipantOrder[];
  price: number;
  candles: SimulationCandle[];
  lastFills: SimulationFill[];
  buyVolume: number;
  sellVolume: number;
  goals: ParticipantGoal[];
  retailActivity: RetailActivity;
}

export function createSimulation(initialSeed = 2026): SimulationState {
  let book: ParticipantOrder[] = [];
  const fills: SimulationFill[] = [];
  for (const incoming of PARTICIPANT_ORDERS) {
    const result = submitLimitOrder(book, incoming);
    book = result.book;
    fills.push(...result.fills);
  }
  const opening = candleFromFills(0, fills);
  let seed = initialSeed >>> 0;
  const random = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  const goals: ParticipantGoal[] = MARKET_PARTICIPANTS.flatMap((participant) =>
    participant.kind === "whale" || participant.kind === "institution"
      ? [sampleParticipantGoal(participant.id, participant.kind, 0, random)] : []);
  const retailActivity = sampleRetailActivity(0, random);
  return {
    update: 0, seed, book, price: opening?.close ?? STARTING_PRICE, goals, retailActivity,
    candles: opening ? [opening] : [], lastFills: fills, buyVolume: 0, sellVolume: 0,
  };
}

export function advanceSimulation(state: SimulationState): SimulationState {
  let seed = state.seed;
  const random = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  let book = state.book;
  const update = state.update + 1;
  const fills: SimulationFill[] = [];
  const retailActivity = update >= state.retailActivity.nextChangeUpdate
    ? sampleRetailActivity(update, random) : state.retailActivity;
  const retailOrders = sampleRetailArrivals(retailActivity.ordersPerSecond, random);
  for (let index = 0; index < retailOrders; index += 1) {
    const participant = MARKET_RETAIL_PARTICIPANTS[Math.floor(random() * MARKET_RETAIL_PARTICIPANTS.length)];
    const side = random() < 0.5 ? "buy" : "sell";
    const result = executeMarketOrder(book, participant.id, side, sampleRetailOrderSize(random));
    book = result.book;
    fills.push(...result.fills);
  }
  // Keep retail market-order counters separate from trades caused by new limits.
  const retailFills = [...fills];
  const execution = advanceParticipantGoals(book, recordGoalFills(state.goals, retailFills, update),
    fills.at(-1)?.price ?? state.price, update, random);
  book = execution.book;
  fills.push(...execution.fills);
  // Stagger maker replacements across a second instead of withdrawing all quotes
  // in one batch. Other makers remain available to provide liquidity.
  const firstMaker = Math.floor(state.update * MARKET_MAKERS.length / UPDATES_PER_SECOND);
  const endMaker = Math.floor(update * MARKET_MAKERS.length / UPDATES_PER_SECOND);
  const makers = Array.from({ length: endMaker - firstMaker }, (_, index) => MARKET_MAKERS[(firstMaker + index) % MARKET_MAKERS.length]);
  const repriced = repriceMarketMakers(book, fills.at(-1)?.price ?? state.price, random, makers);
  book = repriced.book;
  fills.push(...repriced.fills);
  const goals = recordGoalFills(execution.goals, repriced.fills, update);
  const candle = candleFromFills(Math.ceil(update / UPDATES_PER_CANDLE), fills, state.price);
  let candles = state.candles;
  if (candle) {
    const active = candles.at(-1);
    candles = active?.update === candle.update
      ? [...candles.slice(0, -1), {
        ...active,
        high: Math.max(active.high, candle.high),
        low: Math.min(active.low, candle.low),
        close: candle.close,
        volume: active.volume + candle.volume,
      }]
      : [...candles, candle].slice(-400);
  }
  return {
    update, seed, book, goals, retailActivity,
    price: candle?.close ?? state.price,
    candles,
    lastFills: fills,
    buyVolume: state.buyVolume + retailFills.filter((fill) => fill.side === "buy").reduce((sum, fill) => sum + fill.quantity, 0),
    sellVolume: state.sellVolume + retailFills.filter((fill) => fill.side === "sell").reduce((sum, fill) => sum + fill.quantity, 0),
  };
}
