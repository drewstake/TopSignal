import { useMemo } from 'react';
import { buildSimulationDepth, type DepthLevel } from './simulationDepth';
import { PARTICIPANT_GROUPS, type ParticipantOrder } from './simulationPreview';

const quantity = (value: number) => value.toLocaleString('en-US');

function DepthSide({ side, levels, shares, maxLevelShares }: {
  side: 'bid' | 'ask'; levels: DepthLevel[]; shares: number; maxLevelShares: number;
}) {
  const name = side === 'bid' ? 'Bids' : 'Asks';
  const largest = Math.max(0, ...levels.map((level) => level.shares));
  return <section className={`simulation-depth-side simulation-depth-${side}`} aria-label={`${name} depth`}>
    <header><h3>{name} <span>Best price first</span></h3><span>{quantity(shares)} shares</span></header>
    <div className="simulation-depth-scroll" tabIndex={0} role="region" aria-label={`${name} price levels, scroll for more`}>
      <table aria-label={`Simulated ${side} levels`}>
        <thead><tr><th scope="col">Price</th><th scope="col">Shares</th><th scope="col">Orders</th><th scope="col">Total</th></tr></thead>
        <tbody>{levels.length ? levels.map((level) => {
          const description = PARTICIPANT_GROUPS.filter((group) => level.byKind[group.kind])
            .map((group) => `${group.label}: ${quantity(level.byKind[group.kind]!)} shares`).join(' · ');
          return <tr key={level.price} className={level.shares === largest ? 'simulation-depth-largest' : undefined}
            title={`${description}. Cumulative depth from best price: ${quantity(level.cumulative)} shares.`}>
            <th scope="row">${level.price.toFixed(2)}</th>
            <td className="simulation-depth-size"><span className="simulation-depth-bar" aria-hidden="true"
              style={{ width: `${level.shares / maxLevelShares * 100}%` }} /><span>{quantity(level.shares)}</span></td>
            <td>{quantity(level.orders)}</td><td>{quantity(level.cumulative)}</td>
          </tr>;
        }) : <tr><td colSpan={4} className="simulation-depth-empty">No resting {side === 'bid' ? 'buy' : 'sell'} orders</td></tr>}</tbody>
      </table>
    </div>
  </section>;
}

export function SimulationOrderBook({ book, lastPrice }: { book: readonly ParticipantOrder[]; lastPrice: number }) {
  const depth = useMemo(() => buildSimulationDepth(book), [book]);
  return <aside className="simulation-depth" aria-label="Simulation L2 order book">
    <header className="simulation-depth-heading"><div><h2>Order book <span>L2</span></h2><p>BLOX100 · Simulated resting orders</p></div></header>
    <DepthSide side="ask" levels={depth.asks} shares={depth.askShares} maxLevelShares={depth.maxLevelShares} />
    <div className="simulation-depth-spread"><div><span>Last trade</span><strong>${lastPrice.toFixed(2)}</strong></div>
      <div><span>Spread</span><strong>{depth.spread === null ? '—' : `$${depth.spread.toFixed(2)}`}</strong></div></div>
    <DepthSide side="bid" levels={depth.bids} shares={depth.bidShares} maxLevelShares={depth.maxLevelShares} />
    <p className="simulation-depth-note">Bars compare shares at each price on the same scale. Total is cumulative depth. Hover a level for participant sizes.</p>
  </aside>;
}
