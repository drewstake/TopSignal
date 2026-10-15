import { INSTRUMENTS, PLAYER, SETTINGS } from './model';
import type { State } from './model';
import { addAccount, amendProtection, beginReset, cancelOrder, closePosition, flatten, processStops, submitOrder, syncProtection } from './exchange';
import { createSimulation, stepSimulation } from './participants';
import { SimulationClock, initialView, orderMessage, publishView } from './runtime';
import type { Command, View } from './runtime';

let state: State | undefined;
let flags = initialView(0);
let pendingSeed: number | null = null;
let lastWall = performance.now(), lastBroadcast = 0;
const clock = new SimulationClock();
const send = () => { if (state) postMessage(publishView(state, { ...flags, backlog: clock.backlog })); lastBroadcast = performance.now(); };
function initialize(seed: number) {
  state = createSimulation(seed, 0);
  // Player is added only after genuine bot warmup.
  delete state.accounts[PLAYER]; state.accountOrder = state.accountOrder.filter(id => id !== PLAYER);
  const generation = flags.generation + 1;
  flags = { ...initialView(seed), generation }; clock.backlog = 0; pendingSeed = null; send();
}
function remember(result: ReturnType<typeof submitOrder>) {
  flags.error = result.ok ? '' : result.error; flags.notice = result.ok ? orderMessage(result) : '';
  if (result.ok) flags.receipt = result.order;
}
self.onmessage = (event: MessageEvent<Command | { type: 'initialize'; seed: number }>) => {
  const c = event.data;
  if (c.type === 'initialize') { initialize(c.seed); return; }
  if (!state || flags.warming) return;
  // Account elapsed wall time under the prior playback state before changing it.
  const now = performance.now(); if (flags.playing || flags.restarting) clock.accrue(now - lastWall); lastWall = now;
  flags.error = ''; flags.notice = '';
  try {
    if (c.type === 'play') flags.playing = c.playing;
    else if (c.type === 'step' && !flags.playing) stepSimulation(state);
    else if (c.type === 'restart') {
      if (!Number.isSafeInteger(c.seed)) throw new Error('Invalid simulation seed.');
      pendingSeed = c.seed; flags.restarting = true; flags.playing = true; beginReset(state);
      flags.notice = 'Restart requested. Closing all positions through the book before rebuilding the world.';
    } else if (flags.restarting) throw new Error('Restart is waiting for real closing liquidity.');
    else if (c.type === 'order') remember(submitOrder(state, PLAYER, c.request));
    else if (c.type === 'cancel') {
      const o = state.orders[c.id];
      if (o?.protection === 'Take profit') { const p = state.accounts[PLAYER].positions[o.contract]; if (p) amendProtection(state, o.contract, p.id, 'target', null); }
      else if (!cancelOrder(state, PLAYER, c.id)) throw new Error('Order is no longer working.');
      flags.notice = `Order #${c.id} cancelled.`;
    } else if (c.type === 'close') remember(closePosition(state, c.key));
    else if (c.type === 'flatten') {
      const results = flatten(state), latest = results.at(-1);
      if (latest) remember(latest);
      flags.notice = results.length ? results.map(orderMessage).join(' ') : 'All positions are flat. Working orders cancelled.';
    }
    else if (c.type === 'protection') { amendProtection(state, c.key, c.position, c.field, c.dollars); flags.notice = `${c.key} ${c.field === 'stop' ? 'stop loss' : 'take profit'} updated.`; }
    syncProtection(state); processStops(state);
  } catch (error) { flags.error = error instanceof Error ? error.message : 'Unable to process the command.'; }
  finishRestart(); send();
};
function finishRestart() {
  if (!state || pendingSeed === null || INSTRUMENTS.some(key => state!.accounts[PLAYER].positions[key])) return;
  initialize(pendingSeed);
}
function tick() {
  try {
    if (state) {
      const now = performance.now();
      if (flags.warming) {
        const start = performance.now(); let steps = 0;
        while (state.step < SETTINGS.warmup / SETTINGS.step && steps++ < SETTINGS.catchup && performance.now() - start < SETTINGS.budgetMs) stepSimulation(state);
        flags.warmupProgress = state.step / (SETTINGS.warmup / SETTINGS.step);
        if (flags.warmupProgress >= 1) { addAccount(state, PLAYER, 10000, 10, .8, true); flags.warming = false; flags.notice = 'Opening auction and 30 simulated minutes of trading complete.'; }
      } else if (flags.playing || flags.restarting) { clock.accrue(now - lastWall); clock.run(() => stepSimulation(state!), () => performance.now()); finishRestart(); }
      lastWall = now;
      if (performance.now() - lastBroadcast >= SETTINGS.broadcastMs) send();
    }
  } catch (error) { flags = { ...flags, playing: false, error: (error as Error).message }; send(); }
  setTimeout(tick, state && flags.warming ? 0 : 4);
}
setTimeout(tick, 0);
// Explicit worker message shape documents the single authority / immutable publication boundary.
export type WorkerPublication = View;
