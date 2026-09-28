import { describe, expect, it } from 'vitest';
import { advanceSimulation, createSimulation, sampleRetailActivity, sampleRetailArrivals, sampleRetailOrderSize, UPDATES_PER_SECOND } from './simulationPreview';

function seededRandom() {
  let seed = 171;
  return () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  };
}

const draws = (...values: number[]) => {
  let index = 0;
  return () => {
    if (index >= values.length) throw new Error('Unexpected random draw');
    return values[index++];
  };
};

describe('Random retail flow', () => {
  it('keeps most orders small while occasionally allowing larger orders within bounds', () => {
    const random = seededRandom();
    const sizes = Array.from({ length: 10000 }, () => sampleRetailOrderSize(random));
    expect(sizes.every((size) => Number.isInteger(size) && size >= 1 && size <= 40)).toBe(true);
    expect(sizes.filter((size) => size <= 3).length).toBeGreaterThan(8700);
    expect(sizes.filter((size) => size > 10).length).toBeGreaterThan(50);
    expect(sizes.filter((size) => size > 10).length).toBeLessThan(150);
    expect(sampleRetailOrderSize(draws(0, 0))).toBe(1);
    expect(sampleRetailOrderSize(draws(0.999, 0.999))).toBe(40);
  });

  it('produces empty frames and bursts, with higher rates producing more orders on average', () => {
    const quietRandom = seededRandom();
    const busyRandom = seededRandom();
    const frames = 60 * UPDATES_PER_SECOND;
    const quiet = Array.from({ length: frames }, () => sampleRetailArrivals(60, quietRandom));
    const busy = Array.from({ length: frames }, () => sampleRetailArrivals(400, busyRandom));
    const sum = (values: number[]) => values.reduce((total, value) => total + value, 0);
    expect(quiet.includes(0)).toBe(true);
    expect(new Set(busy).size).toBeGreaterThan(5);
    expect(sum(quiet) / 60).toBeGreaterThan(54);
    expect(sum(quiet) / 60).toBeLessThan(66);
    expect(sum(busy) / 60).toBeGreaterThan(360);
    expect(sum(busy) / 60).toBeLessThan(440);
  });

  it('samples different activity levels and durations without changing them on every frame', () => {
    const quiet = sampleRetailActivity(50, draws(0, 0, 0));
    const burst = sampleRetailActivity(50, draws(0.99, 0.99, 0.99));
    expect(quiet).toMatchObject({ mode: 'Quiet', ordersPerSecond: 40, nextChangeUpdate: 50 + 2 * UPDATES_PER_SECOND });
    expect(burst.mode).toBe('Burst');
    expect(burst.ordersPerSecond).toBeGreaterThan(quiet.ordersPerSecond);
    expect(burst.nextChangeUpdate).toBeGreaterThan(quiet.nextChangeUpdate);
    let state = { ...createSimulation(), retailActivity: quiet };
    const original = structuredClone(state);
    state = advanceSimulation(state);
    expect(state.retailActivity).toEqual(quiet);
    const changed = advanceSimulation({ ...state, update: quiet.nextChangeUpdate - 1 });
    expect(changed.retailActivity.nextChangeUpdate).toBeGreaterThan(changed.update);
    expect(original.retailActivity).toEqual(quiet);
  });

  it('supports different live-run seeds and repeatable seeded runs', () => {
    const first = createSimulation(1);
    const second = createSimulation(2);
    expect(advanceSimulation(first)).toEqual(advanceSimulation(createSimulation(1)));
    expect(advanceSimulation(first)).not.toEqual(advanceSimulation(second));
  });
});
