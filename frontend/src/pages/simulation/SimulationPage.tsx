import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '../../components/ui/Icon';
import { SimulationOrderBook } from './SimulationOrderBook';
import { SimulationChart } from './SimulationChart';
import { BASE_SPEED, DEFAULT_CONFIG, MAX_CANDLES, TIMEFRAMES, simulationTime, summarizeBook, summarizeParticipants, visibleOrders,
  type Instrument, type Timeframe } from './simulationPreview';
import { type EnvironmentOverrides } from './simulationControls';
import { getSimulationRuntime } from './simulationRuntime';
import { SimulationActivity, SimulationIndividuals } from './SimulationActivity';
import { decodeSimulationCheckpoint, MAX_CHECKPOINT_BYTES } from './simulationCheckpoint';
import { readPreference, usePanelPreference, writePreference } from './simulationWorkspace';
import { defaultView } from './simulationViewport';
import './SimulationPage.css';

const quantity = (n: number) => n.toLocaleString('en-US');
const signed = (n: number) => (n > 0 ? '+' : '') + quantity(n);
const CONFIG_FIELDS: { key: keyof EnvironmentOverrides; label: string; min: number }[] = [
  { key: 'liquidity', label: 'Maker liquidity', min: 0.1 },
  { key: 'volatility', label: 'Behavioral volatility', min: 0.1 },
  { key: 'whaleFrequency', label: 'Whale frequency', min: 0 },
  { key: 'newsFrequency', label: 'News frequency', min: 0 },
];
const overrides = (c: EnvironmentOverrides): EnvironmentOverrides => ({ liquidity: c.liquidity, volatility: c.volatility, whaleFrequency: c.whaleFrequency, newsFrequency: c.newsFrequency });
export function SimulationPage() {
  const runtime = getSimulationRuntime();
  const { session, savedLocally, storageNotice, savedStep, conflict, advancing } = useSyncExternalStore(runtime.subscribe, runtime.getSnapshot);
  const dispatch = runtime.dispatch, simulation = session.market, instrument = session.instrument;
  const spec = simulation.config.contracts[instrument], book = simulation.books[instrument];
  const price = (book.last ?? book.reference) * spec.tick;
  const candles = book.candles[session.timeframe];
  const [seed, setSeed] = useState(String(simulation.initialSeed));
  const [config, setConfig] = useState(() => overrides(simulation.config));
  const [notice, setNotice] = useState('');
  const [configError, setConfigError] = useState('');
  const [resetPending, setResetPending] = useState(false);
  const [pendingImport, setPendingImport] = useState<{ raw: string; description: string } | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [bookOpen, setBookOpen] = usePanelPreference('book');
  const [participantsOpen, setParticipantsOpen] = usePanelPreference('participants', false);
  const [metricsOpen, setMetricsOpen] = usePanelPreference('metrics', false);
  const [chartHeight, setChartHeight] = useState(() => readPreference('height', 440, (v): v is number => typeof v === 'number' && v >= 300 && v <= 720));
  const [bookWidth, setBookWidth] = useState(() => readPreference('bookWidth', 320, (v): v is number => typeof v === 'number' && v >= 260 && v <= 460));
  const [jump, setJump] = useState<{ time: number; id: number; label?: string }>();
  const marketPanel = useRef<HTMLElement>(null), expandButton = useRef<HTMLButtonElement>(null);
  useEffect(() => { writePreference('height', chartHeight); }, [chartHeight]);
  useEffect(() => { writePreference('bookWidth', bookWidth); }, [bookWidth]);
  useEffect(() => {
    if (!expanded) return;
    const overflow = document.body.style.overflow; document.body.style.overflow = 'hidden';
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setExpanded(false); expandButton.current?.focus(); }
      if (event.key === 'Tab') {
        const nodes = [...(marketPanel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), select, input, summary, [tabindex="0"]') ?? [])].filter(n => n.getClientRects().length);
        const first = nodes[0], last = nodes.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    window.addEventListener('keydown', key); expandButton.current?.focus();
    return () => { document.body.style.overflow = overflow; window.removeEventListener('keydown', key); };
  }, [expanded]);
  const orders = useMemo(() => visibleOrders(simulation, instrument), [simulation, instrument]);
  const market = useMemo(() => summarizeBook(orders, price), [orders, price]);
  const summaries = useMemo(() => summarizeParticipants(simulation, instrument), [simulation, instrument]);
  const spread = market.bid === null || market.ask === null ? null : market.ask - market.bid;
  const depth = market.bidContracts + market.askContracts;
  const imbalance = depth ? (market.bidContracts - market.askContracts) / depth * 100 : 0;
  const buy = book.flow.reduce((sum, f) => sum + f.buy, 0), sell = book.flow.reduce((sum, f) => sum + f.sell, 0);
  const buyPercent = buy + sell ? Math.round(buy / (buy + sell) * 100) : 50;
  const newsActive = simulation.time < simulation.newsUntil;
  const changedConfig = JSON.stringify(config) !== JSON.stringify(overrides(simulation.config));
  const customized = CONFIG_FIELDS.some(f => simulation.config[f.key] !== DEFAULT_CONFIG[f.key]);
  const reset = () => {
    if (!seed.trim() || !Number.isInteger(Number(seed)) || Math.abs(Number(seed)) > 2147483646) { setNotice('Seed must be a whole number from −2147483646 to 2147483646.'); return; }
    setResetPending(true);
  };
  const confirmReset = () => {
    for (const symbol of simulation.config.contractOrder) for (const [tf] of TIMEFRAMES) writePreference(`chart.${symbol}.${tf}`, defaultView());
    dispatch({ type: 'reset', seed: Number(seed) });
    setResetPending(false); setJump(undefined);
    setNotice('Simulation reset. Seed ' + seed + '. Opening auction and 30-minute warmup complete.');
  };
  const exportCheckpoint = () => {
    const url = URL.createObjectURL(new Blob([runtime.exportCheckpoint()], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `topsignal-simulation-v2-seed-${simulation.initialSeed}-step-${simulation.step}.json`; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000); setNotice('Checkpoint download requested. Keep the file for exact local continuation.');
  };
  const renderMarket = (content: ReactNode) => expanded ? createPortal(<div className="simulation-page simulation-portal">{content}</div>, document.body) : content;
  return <div className="simulation-page">
    <header className="simulation-heading">
      <div><p className="simulation-eyebrow">MARKET SANDBOX</p><h1>Simulation</h1><p>Explore how liquidity and trader behavior move a market.</p></div>
      <span className="simulation-preview-badge"><Icon name="pulse" /> Synthetic market</span>
    </header>
    {renderMarket(<section ref={marketPanel} className={`simulation-market${expanded ? ' simulation-expanded' : ''}`} role={expanded ? 'dialog' : undefined} aria-modal={expanded ? true : undefined} aria-label="Simulated contract market">
      <div className="simulation-market-heading">
        <div className="simulation-instrument"><span className="simulation-symbol-mark">B</span>
          <div><h2>{instrument} <span>{spec.name}</span></h2><p>USD · ${spec.tick.toFixed(2)} tick · ${spec.multiplier}/point · simulated contracts</p></div></div>
        <div className="simulation-quote"><strong>${price.toFixed(2)}</strong><span>{book.last === null ? 'Starting reference' : 'Last executed trade'}</span></div>
      </div>
      <div className="simulation-toolbar">
        <button type="button" className="simulation-play" disabled={conflict || advancing !== null} onClick={() => dispatch({ type: 'toggle' })}>
          <span aria-hidden="true">{session.running ? 'Ⅱ' : '▶'}</span> {session.running ? 'Pause' : 'Resume'}
        </button>
        <button disabled={session.running || conflict || advancing !== null} onClick={() => dispatch({ type: 'step', steps: 1 })}>Step 0.25s</button>
        {advancing !== null ? <button onClick={runtime.cancelAdvance}>Cancel advancement</button> : <button disabled={session.running || conflict} onClick={runtime.advanceBoundary}>Next {session.timeframe} boundary</button>}
        <label>Instrument<select aria-label="Simulation instrument" value={instrument} onChange={e => { setJump(undefined); dispatch({ type: 'instrument', instrument: e.target.value as Instrument }); }}>
          {simulation.config.contractOrder.map(key => <option key={key} value={key}>{key} · {simulation.config.contracts[key].name}</option>)}
        </select></label>
        <label>Timeframe<select aria-label="Chart timeframe" value={session.timeframe} onChange={e => { setJump(undefined); dispatch({ type: 'timeframe', timeframe: e.target.value as Timeframe }); }}>
          {TIMEFRAMES.map(([tf]) => <option key={tf} value={tf}>{tf}</option>)}
        </select></label>
        <label>Speed<select aria-label="Simulation speed" value={session.speed} onChange={e => dispatch({ type: 'speed', speed: Number(e.target.value) })}>
          {[0.5, 1, 2, 4].map(speed => <option key={speed} value={speed}>{speed}× · {speed * BASE_SPEED}s/s</option>)}
        </select></label>
        <form className="simulation-seed" onSubmit={e => { e.preventDefault(); reset(); }}>
          <label>Seed<input aria-label="Random seed" type="number" min="-2147483646" max="2147483646" step="1" required value={seed} onChange={e => setSeed(e.target.value)} /></label>
          <button type="submit" disabled={conflict} title="Replace this run with a new opening auction and warmup; confirmation required">Reset</button>
        </form>
        <span className="simulation-clock" aria-label="Simulation elapsed time">{simulationTime(simulation.time)}.{String(Math.round(simulation.time % 1 * 100)).padStart(2, '0')}</span>
        <button ref={expandButton} aria-label={expanded ? 'Exit expanded view' : 'Expand workspace'} aria-pressed={expanded} onClick={() => setExpanded(!expanded)}><span className="simulation-expand-wide">{expanded ? 'Exit expanded view' : 'Expand workspace'}</span><span className="simulation-expand-compact" aria-hidden="true">{expanded ? 'Exit' : 'Expand'}</span></button>
      </div>
      {conflict && <div className="simulation-confirm" role="alert" aria-label="Playback ownership">
        <p>Another simulation client owns this run. <strong>Use this tab</strong> takes over playback and loads the latest saved checkpoint here. Export first to keep this tab's unsaved state.</p>
        <button onClick={exportCheckpoint}>Export current run</button>
        <button onClick={runtime.retryOwnership}>Load saved run / retry ownership</button>
        <button onClick={runtime.takeOwnership}>Use this tab</button>
      </div>}
      {resetPending && <div className="simulation-confirm" role="alertdialog" aria-label="Replace simulation run"><p>Reset replaces all current prices, orders, participant positions, tape and saved continuation with seed <strong>{seed}</strong> and the applied environment settings. The opening auction and 30-minute warmup run again. Export first to keep this run.</p><button onClick={exportCheckpoint}>Export current run</button><button onClick={confirmReset}>Replace run</button><button autoFocus onClick={() => setResetPending(false)}>Cancel reset</button></div>}
      <div className="simulation-scenario-note">
        <span>{simulation.regime.name} · {newsActive ? simulation.news + ' · liquidity 60%' : simulation.news === 'No synthetic news' ? simulation.news : 'Latest: ' + simulation.news}{customized ? ' · manual overrides' : ''}</span>
        <span>{advancing !== null ? `Advancing to ${simulationTime(advancing * .25)} · ${Math.max(0, advancing - simulation.step).toLocaleString()} steps left` : session.running ? 'Running' : 'Paused'} · seed {simulation.initialSeed} · step {simulation.step.toLocaleString()}</span>
      </div>
      <div className="simulation-workspace-tools"><button aria-expanded={metricsOpen} onClick={() => setMetricsOpen(!metricsOpen)}>{metricsOpen ? 'Hide metrics' : 'Show metrics'}</button><button aria-expanded={bookOpen} onClick={() => setBookOpen(!bookOpen)}>{bookOpen ? 'Hide order book' : 'Show order book'}</button>
        <details><summary>Panel sizes</summary><label>Minimum chart height<input aria-label="Minimum chart height" type="range" min="300" max="720" step="10" value={chartHeight} onChange={e => setChartHeight(Number(e.target.value))} />{chartHeight}px</label><label className="simulation-book-size">Book width<input aria-label="Order book width" type="range" min="260" max="460" step="10" value={bookWidth} onChange={e => setBookWidth(Number(e.target.value))} />{bookWidth}px</label></details>
        <span className={savedLocally ? '' : 'simulation-save-error'}>{conflict ? 'Tab conflict · paused' : savedLocally ? `Local save through ${simulationTime((savedStep ?? 0) * .25)}` : 'Save unavailable · memory only'}</span>
      </div>
      {metricsOpen && <dl className="simulation-metrics">
        <div><dt>Spread</dt><dd>{spread === null ? '—' : '$' + spread.toFixed(2)}</dd><span>{spread === null ? 'Waiting for two-sided quotes' : Math.round(spread / spec.tick) + ' ticks between bid & ask'}</span></div>
        <div><dt>Traded volume</dt><dd>{quantity(book.volume)} <small>contracts</small></dd><span>{instrument} executions, including warmup</span></div>
        <div><dt>Depth imbalance</dt><dd className={imbalance >= 0 ? 'simulation-up' : 'simulation-down'}>{depth ? Math.abs(imbalance).toFixed(0) + '% ' + (imbalance >= 0 ? 'bid' : 'ask') : '—'}</dd>
          <span>{quantity(market.bidContracts)} bid / {quantity(market.askContracts)} ask</span></div>
        <div><dt>Trade pressure <small>10s</small></dt><dd>{buy + sell ? buyPercent + '% buy' : '—'}</dd>
          <div className="simulation-pressure" aria-hidden="true"><i style={{ width: buyPercent + '%' }} /></div><span>{buy + sell ? (100 - buyPercent) + '% sell · aggressor volume' : 'Waiting for executions'}</span></div>
      </dl>}
      <div className={`simulation-market-panels${bookOpen ? '' : ' simulation-book-hidden'}`} style={{ '--simulation-book-width': bookWidth + 'px' } as CSSProperties}>
        <div className="simulation-chart-panel">
          <div className="simulation-panel-title"><h2>Price action</h2><span>{session.timeframe} candles · simulated time</span></div>
          <SimulationChart key={`${session.generation}:${instrument}:${session.timeframe}`} runtime={runtime} symbol={instrument} tickSize={spec.tick} timeframe={session.timeframe} seconds={TIMEFRAMES.find(([tf]) => tf === session.timeframe)![1]} height={chartHeight} jump={jump} />
          <div className="simulation-chart-footer"><span><i className={session.running ? '' : 'is-paused'} /> {session.running ? 'Market running' : 'Market paused'}</span><span>Chart navigation never changes engine time · snapshot {simulation.step}</span></div>
        </div>
        {bookOpen && <SimulationOrderBook key={`${session.generation}:${instrument}`} book={orders} lastPrice={price} tickSize={spec.tick} symbol={instrument} />}
      </div>
      <SimulationActivity key={`${session.generation}:${instrument}`} runtime={runtime} instrument={instrument} earliest={candles[0]?.time ?? Infinity} onJump={(time, label) => {
        setJump({ time, label, id: (jump?.id ?? 0) + 1 }); marketPanel.current?.querySelector('.simulation-chart-panel')?.scrollIntoView?.({ block: 'center' });
      }} />
    </section>)}
    <section className="simulation-participants" aria-labelledby="simulation-participants-title">
      <header><div><h2 id="simulation-participants-title">Behind the market</h2><p>{instrument} participants · long and short contract positions</p></div><span>{summaries.reduce((n, g) => n + g.count, 0)} traders · {simulation.participants.length} across all markets</span><button aria-expanded={participantsOpen} onClick={() => setParticipantsOpen(!participantsOpen)}>{participantsOpen ? 'Hide participants' : 'Show participants'}</button></header>
      {participantsOpen && <><div className="simulation-participant-grid">
        {summaries.map(group => <details key={group.kind} className="simulation-participant">
          <summary><span><strong>{group.label}</strong><small>{group.count} traders{group.halted ? ' · ' + group.halted + ' halted' : ''}</small></span>
            <span className={group.netInventory >= 0 ? 'simulation-up' : 'simulation-down'}>{signed(group.netInventory)}<small>net contracts</small></span></summary>
          <div className="simulation-participant-detail"><p>{group.description}</p>
            <dl><div><dt>Contracts bought / sold</dt><dd>{quantity(group.bought)} / {quantity(group.sold)}</dd></div>
              <div><dt>Gross position / resting</dt><dd>{quantity(group.inventory)} / {quantity(group.resting)}</dd></div>
              <div><dt>Closed balance / equity</dt><dd>${quantity(Math.round(group.balance))} / ${quantity(Math.round(group.equity))}</dd></div>
              <div><dt>Realized P&amp;L</dt><dd>${signed(Math.round(group.realized))}</dd></div>
              <div><dt>Orders cancelled</dt><dd>{quantity(group.cancelled)}</dd></div>
              {group.kind === 'Whale' && <div><dt>Scheduled parent quantity</dt><dd>{quantity(group.parentRemaining)}</dd></div>}
            </dl></div>
        </details>)}
      </div><SimulationIndividuals runtime={runtime} instrument={instrument} /></>}
      <details className="simulation-settings">
        <summary>Manual environment overrides <span>{changedConfig ? 'Unapplied changes' : customized ? 'Custom settings active' : 'Reference defaults'}</span></summary>
        <form onSubmit={e => {
          e.preventDefault();
          if (CONFIG_FIELDS.some(f => !Number.isFinite(config[f.key]) || config[f.key] < f.min || config[f.key] > 4)) {
            setConfigError('Enter each multiplier within its limits.'); return;
          }
          setConfigError(''); dispatch({ type: 'configure', overrides: config });
          setNotice('Environment overrides applied. Existing positions and chart history retained.');
        }}>
          <div className="simulation-config-grid">{CONFIG_FIELDS.map(field => <label key={field.key}><span>{field.label} (×)</span>
            <input type="number" required min={field.min} max={4} step={0.1} value={Number.isNaN(config[field.key]) ? '' : config[field.key]}
              aria-label={`${field.label} (×)`}
              onChange={e => setConfig(current => ({ ...current, [field.key]: e.target.valueAsNumber }))} />
            <small>{field.min}–4 · applied {simulation.config[field.key]}× · {field.key === 'liquidity' ? 'Scales future maker quote sizes.' : field.key === 'volatility' ? 'Scales behavioral noise and fair-value motion.' : field.key === 'whaleFrequency' ? 'Changes future whale scheduling; 0 disables new whale activity.' : 'Changes future news scheduling; 0 disables new news.'}</small>
          </label>)}</div>
          {configError && <p className="simulation-config-error" role="alert">{configError}</p>}
          <div className="simulation-settings-footer"><p>Defaults are 1×. Overrides change future decisions and schedules. Each bot has randomized capital, contract limits, margin risk and order sizes; trading changes its balance and signed position.</p>
            <button type="button" onClick={() => setConfig(overrides(DEFAULT_CONFIG))}>Default values</button>
            <button type="submit" disabled={conflict || !changedConfig} className="simulation-apply">Apply changes</button></div>
        </form>
      </details>
    </section>
    <section className="simulation-checkpoints" aria-label="Local checkpoints"><h2>Local continuation</h2><p>Version 2 files include the complete engine, RNG and pending orders. Import replaces this run and its saved checkpoint, then pauses. Workspace preferences are separate. Nothing is uploaded.</p>
      <div className="simulation-activity-controls"><button onClick={exportCheckpoint}>Export checkpoint</button><label className="simulation-import">Choose checkpoint<input aria-label="Import checkpoint file" type="file" accept="application/json,.json" disabled={conflict} onChange={async e => {
        const file = e.target.files?.[0]; e.target.value = ''; if (!file) return; setPendingImport(null);
        if (file.size > MAX_CHECKPOINT_BYTES) { setNotice('Checkpoint file exceeds the 4.5 MB limit. Current run unchanged.'); return; }
        try { const raw = await file.text(), parsed = decodeSimulationCheckpoint(raw); if (!parsed) { setNotice('Invalid or incompatible checkpoint. Current run unchanged.'); return; }
          setPendingImport({ raw, description: `Seed ${parsed.session.market.initialSeed}, ${simulationTime(parsed.session.market.time)}, ${parsed.session.market.step.toLocaleString()} steps` });
        } catch { setNotice('Could not read that file. Current run unchanged.'); }
      }} /></label></div>
      {pendingImport && <div className="simulation-confirm" role="alertdialog" aria-label="Import replacement checkpoint"><p>{pendingImport.description}. Import replaces all current simulation state and its saved continuation. Export the current run to keep a copy.</p><button onClick={exportCheckpoint}>Export current run</button><button onClick={() => {
        const result = runtime.importCheckpoint(pendingImport.raw); setNotice(result.message);
        if (result.ok) { const next = runtime.getSnapshot().session; setSeed(String(next.market.initialSeed)); setConfig(overrides(next.market.config)); setJump(undefined); setPendingImport(null); }
      }}>Import and replace run</button><button autoFocus onClick={() => setPendingImport(null)}>Cancel import</button></div>}
      {storageNotice && <p role="alert">{storageNotice}</p>}{notice && <p role="status" className="simulation-feedback">{notice}</p>}
    </section>
    <details className="simulation-explainer"><summary>How this market works</summary>
      <p>All prices come from bot executions, matched by best price then arrival order at the resting price. Unfilled market quantity is cancelled; limit orders wait until filled, cancelled or expired. Bot stops and targets trade in the book. Margin and inventory limits can reject new exposure or force liquidation.</p>
      <p>Shared regimes and synthetic news influence fair value and participant decisions. They never set trade prices. Each candle opens at its first execution, with wicks and volume from that bucket only. Empty windows have no candle. All five timeframes are built independently from the same trades.</p>
      <p>All three markets run in order on one random stream. Switching views never changes that stream. The opening auction and 30-minute warmup use the same engine, followed by fixed 0.25-second steps at 18 simulated seconds per real second at 1× speed.</p>
      <p>The run and latest {MAX_CANDLES.toLocaleString()} candles per series are saved in this browser. Refresh and navigation keep the run; hidden or closed tabs do not accumulate catch-up work. Only Reset starts over. The same seed, configuration and steps reproduce the same sequence. No real orders are sent.</p>
    </details>
    <p className="simulation-isolation">Synthetic prices and participants · Isolated from your accounts and live trading<br />
      {savedLocally ? 'History saved in this browser' : 'Browser save unavailable · this run is kept in memory'}</p>
  </div>;
}
