import { describe, expect, it } from 'vitest';
import { advanceParticipantGoals, createSimulation, executeMarketOrder, recordGoalFills, sampleParticipantGoal, UPDATES_PER_SECOND, type ParticipantGoal, type ParticipantOrder } from './simulationPreview';

const goal = (overrides: Partial<ParticipantGoal> = {}): ParticipantGoal => ({
  participantId: 'whale-1', kind: 'whale', side: 'buy', target: 1000,
  filled: 0, nextActionUpdate: 1, completedGoals: 0, totalFilled: 0,
  style: 'Balanced', intervalUpdates: 160, pauseUpdates: 10 * UPDATES_PER_SECOND, priceTolerance: 0.01, ...overrides,
});
const quote = (id: string, side: 'buy' | 'sell', quantity: number, price = 5): ParticipantOrder => ({
  id, participantId: id, kind: 'retail', side, quantity, price,
});
const sequence = (...values: number[]) => {
  let index = 0;
  return () => {
    if (index >= values.length) throw new Error('Unexpected random draw');
    return values[index++];
  };
};

describe('Whale and institution execution goals', () => {
  it('starts finite goals with staggered arrivals instead of placing the entire target on L2', () => {
    const state = createSimulation();
    expect(state.goals).toHaveLength(160);
    expect(state.goals.filter((g) => g.kind === 'whale').every((g) => g.target >= 500 && g.target <= 2000)).toBe(true);
    expect(state.goals.filter((g) => g.kind === 'institution').every((g) => g.target >= 100 && g.target <= 600)).toBe(true);
    expect(new Set(state.goals.map((g) => g.target)).size).toBeGreaterThan(50);
    expect(new Set(state.goals.map((g) => g.style)).size).toBe(3);
    expect(new Set(state.goals.map((g) => g.nextActionUpdate)).size).toBeGreaterThan(1);
    expect(state.book.some((order) => order.kind === 'whale' || order.kind === 'institution')).toBe(false);
    expect(state).toEqual(createSimulation());
  });

  it('takes small slices in the same direction across updates and leaves input unchanged', () => {
    const book = [quote('seller', 'sell', 1000)];
    const goals = [goal()];
    const original = structuredClone({ book, goals });
    const first = advanceParticipantGoals(book, goals, 5, 1, sequence(0, 0, 0));
    expect(first.fills).toEqual([{ takerId: 'whale-1', makerId: 'seller', side: 'buy', price: 5, quantity: 50 }]);
    expect(first.goals[0]).toMatchObject({ filled: 50, side: 'buy', nextActionUpdate: 1 + 2 * UPDATES_PER_SECOND });
    const waiting = advanceParticipantGoals(first.book, first.goals, 5, 2, sequence());
    expect(waiting.fills).toEqual([]);
    const second = advanceParticipantGoals(waiting.book, waiting.goals, 5, 1 + 2 * UPDATES_PER_SECOND, sequence(0.99, 0, 0));
    expect(second.goals[0]).toMatchObject({ filled: 199, side: 'buy' });
    expect({ book, goals }).toEqual(original);
  });

  it('credits partial resting fills and cancels outstanding slices before taking the remaining goal', () => {
    const resting = advanceParticipantGoals([], [goal({ filled: 940 })], 5, 1, sequence(0.9, 0, 0.9, 0));
    expect(resting.fills).toEqual([]);
    expect(resting.book[0]).toMatchObject({ quantity: 60, price: 4.95 });
    expect(resting.goals[0].filled).toBe(940);
    const sell = executeMarketOrder(resting.book, 'seller', 'sell', 40);
    const credited = recordGoalFills(resting.goals, sell.fills, 2);
    expect(credited[0].filled).toBe(980);
    const finish = advanceParticipantGoals([...sell.book, quote('ask', 'sell', 100)], credited, 5, 1 + 2 * UPDATES_PER_SECOND, sequence(0.9, 0, 0));
    expect(finish.fills[0].quantity).toBe(20);
    expect(finish.book.some((order) => order.participantId === 'whale-1')).toBe(false);
    expect(finish.goals[0]).toMatchObject({ filled: 1000, completedGoals: 1, nextActionUpdate: 1 + 12 * UPDATES_PER_SECOND });
  });

  it('pauses completed goals, then begins a new finite goal that can change direction', () => {
    const completed = goal({ filled: 1000, completedGoals: 1, totalFilled: 1000, nextActionUpdate: 1 + 12 * UPDATES_PER_SECOND });
    const book = [quote('bid', 'buy', 1000)];
    expect(advanceParticipantGoals(book, [completed], 5, 12 * UPDATES_PER_SECOND, sequence()).fills).toEqual([]);
    const next = advanceParticipantGoals(book, [completed], 5, 1 + 12 * UPDATES_PER_SECOND, sequence(0.5, 0.9, 0.5, 0, 0, 0, 0, 0, 0, 0));
    expect(next.goals[0]).toMatchObject({ side: 'sell', target: 1250, filled: 63, completedGoals: 1, totalFilled: 1063 });
    expect(next.fills[0]).toMatchObject({ side: 'sell', quantity: 63 });
  });

  it('accounts for both goals in a trade before the second participant can submit again', () => {
    const seller = goal({ participantId: 'institution-1', kind: 'institution', side: 'sell', target: 250, filled: 240 });
    const book: ParticipantOrder[] = [{ ...quote('institution-1', 'sell', 10), kind: 'institution' }];
    const result = advanceParticipantGoals(book, [goal(), seller], 5, 1, sequence(0, 0, 0));
    expect(result.goals[0].filled).toBe(10);
    expect(result.goals[1]).toMatchObject({ filled: 250, completedGoals: 1, nextActionUpdate: 1 + 10 * UPDATES_PER_SECOND });
    expect(result.fills).toHaveLength(1);
  });

  it('does not count submitted or unavailable shares as fills', () => {
    const empty = advanceParticipantGoals([], [goal()], 5, 1, sequence(0, 0, 0));
    expect(empty.fills).toEqual([]);
    expect(empty.goals[0].filled).toBe(0);
    const partial = advanceParticipantGoals([quote('ask', 'sell', 7)], [goal()], 5, 1, sequence(0, 0, 0));
    expect(partial.goals[0].filled).toBe(7);
    expect(partial.book.every((order) => order.quantity === 0)).toBe(true);
  });

  it('gives patient traders smaller passive slices and longer rests than urgent traders', () => {
    const book = [quote('ask', 'sell', 1000, 5.01), quote('bid', 'buy', 1000, 4.99)];
    const patient = goal({ style: 'Patient', intervalUpdates: 600, priceTolerance: 0.002 });
    const resting = advanceParticipantGoals(book, [patient], 5, 1, sequence(0, 0, 0.5, 0.5));
    const bid = resting.book.find((order) => order.participantId === patient.participantId)!;
    expect(resting.fills).toEqual([]);
    expect(bid.quantity).toBe(20);
    expect(bid.price).toBeLessThan(5.01);
    const waiting = advanceParticipantGoals(resting.book, resting.goals, 5.1, 50, sequence());
    expect(waiting.book.find((order) => order.id === bid.id)).toBe(bid);
    const urgent = goal({ style: 'Urgent', intervalUpdates: 30, priceTolerance: 0.01 });
    const taking = advanceParticipantGoals(book, [urgent], 5, 1, sequence(0, 0, 0.5));
    expect(taking.fills[0]).toMatchObject({ price: 5.01, quantity: 100 });
    expect(taking.goals[0].nextActionUpdate).toBeLessThan(resting.goals[0].nextActionUpdate);
  });

  it('respects individual price tolerance on both buys and sells', () => {
    for (const side of ['buy', 'sell'] as const) {
      const opposite = side === 'buy' ? 'sell' : 'buy';
      const book = [quote('near', opposite, 20, side === 'buy' ? 5.01 : 4.99),
        quote('far', opposite, 100, side === 'buy' ? 5.04 : 4.96)];
      const narrow = goal({ side, style: 'Urgent', priceTolerance: 0.002 });
      const limited = advanceParticipantGoals(book, [narrow], 5, 1, sequence(0, 0, 0));
      const wide = advanceParticipantGoals(book, [{ ...narrow, priceTolerance: 0.01 }], 5, 1, sequence(0, 0, 0));
      expect(limited.goals[0].filled).toBe(20);
      expect(limited.book.find((order) => order.id === 'far')!.quantity).toBe(100);
      expect(wide.goals[0].filled).toBe(100);
    }
  });

  it('lets an urgent trader finish a small remainder without exceeding the goal', () => {
    const urgent = goal({ style: 'Urgent', filled: 850, totalFilled: 850 });
    const result = advanceParticipantGoals([quote('seller', 'sell', 1000)], [urgent], 5, 1, sequence(0, 0, 0));
    expect(result.fills[0].quantity).toBe(150);
    expect(result.goals[0]).toMatchObject({ filled: 1000, totalFilled: 1000, completedGoals: 1 });
  });

  it('samples different patience, targets, pauses and willingness to pay at the profile extremes', () => {
    const patient = sampleParticipantGoal('whale-1', 'whale', 0, () => 0);
    const urgent = sampleParticipantGoal('whale-2', 'whale', 0, () => 0.999);
    expect(patient).toMatchObject({ style: 'Patient', target: 500 });
    expect(urgent.style).toBe('Urgent');
    expect(urgent.target).toBeGreaterThan(patient.target);
    expect(urgent.intervalUpdates).toBeLessThan(patient.intervalUpdates);
    expect(urgent.priceTolerance).toBeGreaterThan(patient.priceTolerance);
    expect(urgent.pauseUpdates).not.toBe(patient.pauseUpdates);
  });

});
