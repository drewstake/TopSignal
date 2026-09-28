import { useEffect, useMemo, useState } from "react";
import { Icon } from "../../components/ui/Icon";
import { SimulationOrderBook } from "./SimulationOrderBook";
import { SimulationChart } from "./SimulationChart";
import { advanceSimulation, createSimulation, MARKET_PARTICIPANTS, MARKET_RETAIL_PARTICIPANTS, summarizeBook, summarizeParticipants, UPDATE_INTERVAL_MS } from "./simulationPreview";
import "./SimulationPage.css";

export function SimulationPage() {
  const [simulation, setSimulation] = useState(() => createSimulation(window.crypto.getRandomValues(new Uint32Array(1))[0]));
  useEffect(() => {
    let previous = performance.now();
    let accumulated = 0;
    let frame = 0;
    const tick = (now: number) => {
      // Bound catch-up after backgrounding; never queue a long burst of stale trades.
      accumulated += Math.min(100, Math.max(0, now - previous));
      previous = now;
      const steps = Math.floor((accumulated + 1e-7) / UPDATE_INTERVAL_MS);
      accumulated -= steps * UPDATE_INTERVAL_MS;
      if (steps > 0) setSimulation((state) => {
        let next = state;
        for (let index = 0; index < steps; index += 1) next = advanceSimulation(next);
        return next;
      });
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const market = useMemo(() => summarizeBook(simulation.book, simulation.price), [simulation.book, simulation.price]);
  const summaries = useMemo(() => summarizeParticipants(simulation.book), [simulation.book]);
  const quoteRange = (low: number | null, high: number | null) => low === null || high === null
    ? "No quotes" : `$${low.toFixed(2)}–$${high.toFixed(2)}`;

  return <div className="simulation-page">
    <header className="simulation-heading">
      <div><p className="simulation-eyebrow">MARKET SANDBOX</p><h1>Simulation</h1>
        <p>Retail trades and ongoing whale and institution orders fill against participant bids and asks.</p></div>
      <span className="simulation-preview-badge"><Icon name="pulse" /> Running</span>
    </header>

    <section className="simulation-market" aria-label="Simulated stock market">
      <div className="simulation-market-heading">
        <div className="simulation-instrument"><span className="simulation-symbol-mark">B</span>
          <div><h2>BLOX100 <span>SIMULATED</span></h2><p>Simulated stock · USD</p></div></div>
        <div className="simulation-quote"><strong>${market.price.toFixed(2)}</strong>
          <span>{simulation.candles.length ? 'Last trade price' : 'Starting price · Waiting for orders'}</span></div>
      </div>
      <dl className="simulation-order-quotes" aria-label="Resting buy and sell orders">
        <div><dt>Resting bids</dt><dd className="simulation-up">{quoteRange(market.lowestBid, market.bid)}</dd><span>{market.buyers} buyers · {market.bidShares.toLocaleString('en-US')} shares bid</span></div>
        <div><dt>Resting asks</dt><dd className="simulation-down">{quoteRange(market.ask, market.highestAsk)}</dd><span>{market.sellers} sellers · {market.askShares.toLocaleString('en-US')} shares offered</span></div>
      </dl>
      <div className="simulation-market-panels">
      <div className="simulation-chart-panel">
      {simulation.candles.length ? <SimulationChart candles={simulation.candles} />
        : <p className="simulation-waiting" role="status">Waiting for the first market orders…</p>}
      <div className="simulation-chart-footer"><span><i /> Running continuously</span><span>Hover or use ← → to inspect</span></div>
      </div>
      <SimulationOrderBook book={simulation.book} lastPrice={simulation.price} />
      </div>
    </section>
    <section className="simulation-participants" aria-labelledby="simulation-participants-title">
      <header><h2 id="simulation-participants-title">Market participants</h2><span>{(MARKET_PARTICIPANTS.length + MARKET_RETAIL_PARTICIPANTS.length).toLocaleString('en-US')} participants</span></header>
      <p>Illustrative order sizes. Market makers refresh quotes in rotation within ±1% of the latest trade price.</p>
      <div className="simulation-participant-grid">
        {summaries.map((group) => {
          const goals = simulation.goals.filter((goal) => goal.kind === group.kind);
          const active = goals.filter((goal) => goal.filled < goal.target);
          return <article key={group.kind} aria-label={group.label}>
          <h3>{group.label}<span>{group.count}</span></h3>
          <p>{goals.length
            ? `${active.filter((goal) => goal.side === 'buy').length} buy goals · ${active.filter((goal) => goal.side === 'sell').length} sell goals`
            : `${group.buyers} buying · ${group.sellers} selling`}</p>
          <strong>{goals.length ? `${Math.min(...goals.map((goal) => goal.target)).toLocaleString('en-US')}–${Math.max(...goals.map((goal) => goal.target)).toLocaleString('en-US')}` : group.orderSize.toLocaleString('en-US')} <small>{group.orderSize === 1 ? 'share' : 'shares'} per {goals.length ? 'goal' : 'order'}</small></strong>
          <dl><div><dt>Shares bid</dt><dd>{group.bidShares.toLocaleString('en-US')}</dd></div>
            <div><dt>Shares offered</dt><dd>{group.askShares.toLocaleString('en-US')}</dd></div>
            {goals.length > 0 && <>
              <div><dt>Patient / balanced / urgent</dt><dd>{goals.filter((goal) => goal.style === 'Patient').length} / {goals.filter((goal) => goal.style === 'Balanced').length} / {goals.filter((goal) => goal.style === 'Urgent').length}</dd></div>
              <div><dt>Current goals filled</dt><dd>{goals.reduce((sum, goal) => sum + goal.filled, 0).toLocaleString('en-US')} / {goals.reduce((sum, goal) => sum + goal.target, 0).toLocaleString('en-US')}</dd></div>
              <div><dt>Goals completed</dt><dd>{goals.reduce((sum, goal) => sum + goal.completedGoals, 0).toLocaleString('en-US')}</dd></div>
            </>}
          </dl>
        </article>;
        })}
        <article aria-label="Retail market orders">
          <h3>Retail market orders<span>{MARKET_RETAIL_PARTICIPANTS.length}</span></h3>
          <p>Random buys and sells · 50/50 chance</p>
          <strong>1–3 <small>shares usually · up to 40</small></strong>
          <dl><div><dt>Activity</dt><dd>{simulation.retailActivity.mode}</dd></div>
            <div><dt>Shares bought</dt><dd>{simulation.buyVolume.toLocaleString('en-US')}</dd></div>
            <div><dt>Shares sold</dt><dd>{simulation.sellVolume.toLocaleString('en-US')}</dd></div></dl>
        </article>
      </div>
    </section>
    <p className="simulation-note">Retail order arrivals vary randomly through quiet stretches and bursts. 90% of orders are 1–3 shares, 9% are 4–10, and 1% are 11–40; buys and sells each have a 50% chance. Whales target 500–2,000 shares and institutions 100–600. Each goal gets random patience and price tolerance: patient traders wait at limits, while urgent traders take larger slices within their price limits. Completed goals pause before choosing a new target and style. Each candle opens at the previous close; highs, lows and volume include completed trades. Limit prices vary across ±1% in one-cent steps. Market makers refresh in rotation and replenish filled quotes without inventory limits.</p>
  </div>;
}
