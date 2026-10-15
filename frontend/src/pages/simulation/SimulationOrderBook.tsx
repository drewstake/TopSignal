import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { buildPriceLadder, LADDER_RADIUS, type DepthLevel } from './simulationDepth';
import { PARTICIPANT_GROUPS, type ParticipantOrder } from './simulationPreview';
import { wheelPixels } from './simulationViewport';

import { quantity } from './simulationFormat';
function LiquidityCell({ level, side, scale, wall, inspect }: { level?: DepthLevel; side: 'bid' | 'ask'; scale: number; wall: number; inspect: (side: 'bid' | 'ask', price: number) => void }) {
  const description = level ? PARTICIPANT_GROUPS.filter(g => level.byKind[g.kind])
    .map(g => g.label + ': ' + quantity(level.byKind[g.kind]!) + ' contracts').join(' · ') : 'No resting orders';
  return <td className={'simulation-ladder-size simulation-ladder-' + side + (level && level.contracts >= wall ? ' is-wall' : '')}
    title={description + (level ? '. ' + level.orders + ' orders; cumulative depth ' + quantity(level.cumulative) + ' contracts.' : '')}>
    <i aria-hidden="true" style={{ width: ((level?.contracts ?? 0) / scale * 100) + '%' }} />
    {level ? <button className="simulation-level-inspect" aria-label={`Inspect ${side} at ${level.price.toFixed(2)}: ${level.contracts} contracts; cumulative ${level.cumulative}`} onClick={() => inspect(side, level.price)}>{quantity(level.contracts)} <small>Σ {quantity(level.cumulative)}</small></button> : <span className="simulation-empty-tick">·</span>}
  </td>;
}
export const SimulationOrderBook = memo(function SimulationOrderBook({ book, lastPrice, tickSize = 0.25, symbol = 'BX', active = true }: { book: readonly ParticipantOrder[]; lastPrice: number; tickSize?: number; symbol?: string; active?: boolean }) {
  const lastTicks = Math.round(lastPrice / tickSize);
  const [center, setCenter] = useState(() => Math.max(LADDER_RADIUS + 1, lastTicks));
  const [following, setFollowing] = useState(true);
  const latestPrice = useRef(lastTicks);
  const ladder = useRef<HTMLDivElement>(null);
  const [inspection, setInspection] = useState<{ side: 'bid' | 'ask'; price: number } | null>(null);
  useEffect(() => {
    const target = ladder.current; if (!target || !active) return;
    let remainder = 0;
    const wheel = (event: WheelEvent) => {
      event.preventDefault(); remainder += wheelPixels(event.deltaY, event.deltaMode, target.clientHeight);
      const ticks = Math.trunc(remainder / 20); if (!ticks) return;
      remainder -= ticks * 20; setFollowing(false); setCenter(c => Math.max(LADDER_RADIUS + 1, c - ticks));
    };
    target.addEventListener('wheel', wheel, { passive: false }); return () => target.removeEventListener('wheel', wheel);
  }, [active]);
  useEffect(() => { latestPrice.current = lastTicks; }, [lastTicks]);
  const nearEdge = Math.abs(lastTicks - center) > LADDER_RADIUS - 3;
  useEffect(() => {
    if (!active || !following || !nearEdge) return;
    // A short dwell avoids chasing every trade; price changes do not restart it.
    const timer = window.setTimeout(() => setCenter(Math.max(LADDER_RADIUS + 1, latestPrice.current)), 400);
    return () => window.clearTimeout(timer);
  }, [active, following, nearEdge, center]);
  const depth = useMemo(() => active ? buildPriceLadder(book, center, tickSize) : null, [active, book, center, tickSize]);
  const page = (direction: number) => {
    setFollowing(false);
    setCenter(c => Math.max(LADDER_RADIUS + 1, c + direction * LADDER_RADIUS));
  };
  const follow = () => { setFollowing(true); setCenter(Math.max(LADDER_RADIUS + 1, lastTicks)); };
  if (!depth) return null;
  const bestBid = depth.bids[0]?.price;
  const bestAsk = depth.asks[0]?.price;
  const inspected = inspection && (inspection.side === 'bid' ? depth.bids : depth.asks).find(l => l.price === inspection.price);
  const inspect = (side: 'bid' | 'ask', price: number) => setInspection({ side, price });
  return <aside className="simulation-depth" aria-label={`${symbol} simulation L2 order book`}>
    <header className="simulation-depth-heading"><h2>Order book <span>L2</span></h2>
      <button type="button" aria-pressed={following} onClick={() => following ? setFollowing(false) : follow()}>Follow price</button></header>
    <div className="simulation-depth-spread"><span>Last <strong>${lastPrice.toFixed(2)}</strong></span>
      <span>Spread <strong>{depth.spread === null ? '—' : '$' + depth.spread.toFixed(2)}</strong></span></div>
    <div className="simulation-depth-spread"><span>Best bid <strong>{bestBid?.toFixed(2) ?? 'None'}</strong></span><span>Best ask <strong>{bestAsk?.toFixed(2) ?? 'None'}</strong></span></div>
    <div ref={ladder} className="simulation-ladder" tabIndex={0} role="region" aria-label="Price ladder. Arrow keys browse deeper levels; Home follows price."
      onKeyDown={event => {
        if (['ArrowUp', 'PageUp', 'ArrowDown', 'PageDown', 'Home'].includes(event.key)) event.preventDefault();
        if (event.key === 'ArrowUp' || event.key === 'PageUp') page(1);
        if (event.key === 'ArrowDown' || event.key === 'PageDown') page(-1);
        if (event.key === 'Home') follow();
      }}>
      <table aria-label="Simulated price ladder"><colgroup><col /><col className="simulation-price-column" /><col /></colgroup>
        <thead><tr><th scope="col">Bid contracts</th><th scope="col">Price ($)</th><th scope="col">Ask contracts</th></tr></thead>
        <tbody>{depth.rows.map(row => <tr key={row.ticks} data-price={row.price.toFixed(2)}
          className={(row.ticks === lastTicks ? 'is-current ' : '') + (bestBid !== undefined && bestAsk !== undefined && row.price > bestBid && row.price < bestAsk ? 'is-spread' : '')}
          aria-label={'Price $' + row.price.toFixed(2) + (row.ticks === lastTicks ? ', last trade' : '')}>
          <LiquidityCell level={row.bid} side="bid" scale={depth.maxLevelContracts} wall={depth.wallThreshold} inspect={inspect} />
          <th scope="row" className={(row.price === bestBid ? 'is-best-bid ' : '') + (row.price === bestAsk ? 'is-best-ask' : '')}>
            {row.ticks === lastTicks && <span className="simulation-last-arrow" aria-hidden="true">›</span>}{row.price.toFixed(2)}
          </th>
          <LiquidityCell level={row.ask} side="ask" scale={depth.maxLevelContracts} wall={depth.wallThreshold} inspect={inspect} />
        </tr>)}</tbody>
      </table>
    </div>
    <div className="simulation-ladder-nav"><button type="button" onClick={() => page(1)} aria-label="Show higher prices">↑ Higher</button>
      <button type="button" onClick={follow}>Recenter</button>
      <button type="button" disabled={center <= LADDER_RADIUS + 1} onClick={() => page(-1)} aria-label="Show lower prices">↓ Lower</button></div>
    {inspection && <div className="simulation-book-inspection"><button aria-label="Close level inspection" onClick={() => setInspection(null)}>×</button><strong>{inspection.side} ${inspection.price.toFixed(2)}</strong><p>{inspected ? `${inspected.contracts} contracts · ${inspected.orders} orders · cumulative ${inspected.cumulative}` : 'No resting orders remain at this level.'}</p>{inspected && PARTICIPANT_GROUPS.filter(g => inspected.byKind[g.kind]).map(g => <p key={g.kind}>{g.label}: {inspected.byKind[g.kind]} contracts</p>)}</div>}
    <p className="simulation-depth-note">{!depth.bidContracts && !depth.askContracts ? 'No resting orders. ' : !depth.bidContracts ? 'No resting bids. ' : !depth.askContracts ? 'No resting asks. ' : ''}{following ? 'Following price with edge delay' : 'Manual browsing'} · Wheel / keys browse · Σ cumulative contracts · Select a level for contributors</p>
  </aside>;
}, (previous, next) => next.active === false && previous.active === false);
