import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '../../components/ui/Icon';
import { APP_SCALE_CHANGED_EVENT, getAppScale } from '../../lib/appScale';
import { SimulationDialog } from './SimulationDialog';
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
import { quantity } from './simulationFormat';
import './SimulationPage.css';

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
  const [sidebarOpen, setSidebarOpen] = usePanelPreference('sidebar', true);
  const [panel, setPanel] = useState('participants');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const page = useRef<HTMLDivElement>(null);
  const sideContent = useRef<HTMLDivElement>(null), wasExpanded = useRef(false);
  const [chartHeight, setChartHeight] = useState(() => readPreference('height', 440, (v): v is number => typeof v === 'number' && v >= 300 && v <= 720));
  const [bookWidth, setBookWidth] = useState(() => readPreference('bookWidth', 320, (v): v is number => typeof v === 'number' && v >= 260 && v <= 460));
  const [jump, setJump] = useState<{ time: number; id: number; label?: string }>();
  const marketPanel = useRef<HTMLElement>(null), expandButton = useRef<HTMLButtonElement>(null);
  useEffect(() => { writePreference('height', chartHeight); }, [chartHeight]);
  useEffect(() => { writePreference('bookWidth', bookWidth); }, [bookWidth]);
  useEffect(() => { if (sideContent.current) sideContent.current.scrollTop = 0; }, [panel]);
  useEffect(() => {
    if (!expanded) {
      if (wasExpanded.current) expandButton.current?.focus({ preventScroll: true });
      wasExpanded.current = false;
      return;
    }
    wasExpanded.current = true;
    const overflow = document.body.style.overflow; document.body.style.overflow = 'hidden';
    const key = (event: KeyboardEvent) => {
      if (document.querySelector('dialog[open]')) return;
      if (event.key === 'Escape') setExpanded(false);
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
  useEffect(() => {
    const root = page.current;
    if (!root) return;
    const fit = () => root.style.setProperty('--simulation-available-height', `${Math.max(400, (window.innerHeight - root.getBoundingClientRect().top) / getAppScale() - 12)}px`);
    const observer = new ResizeObserver(fit);
    observer.observe(root.parentElement ?? root);
    const header = document.querySelector('.workspace-topbar');
    if (header) observer.observe(header);
    window.addEventListener(APP_SCALE_CHANGED_EVENT, fit);
    window.addEventListener('resize', fit); fit();
    return () => { observer.disconnect(); window.removeEventListener(APP_SCALE_CHANGED_EVENT, fit); window.removeEventListener('resize', fit); };
  }, []);
  const orders = useMemo(() => visibleOrders(simulation, instrument), [simulation, instrument]);
  const market = useMemo(() => summarizeBook(orders, price), [orders, price]);
  const summaries = useMemo(() => summarizeParticipants(simulation, instrument), [simulation, instrument]);
  const spread = market.bid === null || market.ask === null ? null : market.ask - market.bid;
  const depth = market.bidContracts + market.askContracts;
  const imbalance = depth ? (market.bidContracts - market.askContracts) / depth * 100 : 0;
  const buy = book.flow.reduce((sum, f) => sum + f.buy, 0), sell = book.flow.reduce((sum, f) => sum + f.sell, 0);
  const buyPercent = buy + sell ? Math.round(buy / (buy + sell) * 100) : 50;
  const newsActive = simulation.time < simulation.newsUntil;
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
  const environmentControls = (fields: typeof CONFIG_FIELDS) => {
    const scope = fields.map(f => f.key).join(',');
    const changed = fields.some(f => config[f.key] !== simulation.config[f.key]);
    return <form className="simulation-environment" onSubmit={e => {
      e.preventDefault();
      if (fields.some(f => !Number.isFinite(config[f.key]) || config[f.key] < f.min || config[f.key] > 4)) {
        setConfigError(scope); return;
      }
      const applied = overrides(simulation.config);
      for (const field of fields) applied[field.key] = config[field.key];
      setConfigError(''); dispatch({ type: 'configure', overrides: applied });
      setNotice('Environment overrides applied. Existing positions and chart history retained.');
    }}>
      <div className="simulation-config-grid">{fields.map(field => <label key={field.key}>
        <span title={field.key === 'liquidity' ? 'Scales future maker quote sizes.' : field.key === 'volatility' ? 'Scales behavioral noise and fair-value motion.' : field.key === 'whaleFrequency' ? 'Changes future whale scheduling; 0 disables new whale activity.' : 'Changes future news scheduling; 0 disables new news.'}>{field.label} (×)</span>
        <input type="number" required min={field.min} max={4} step={0.1} value={Number.isNaN(config[field.key]) ? '' : config[field.key]} aria-label={`${field.label} (×)`}
          onChange={e => setConfig(current => ({ ...current, [field.key]: e.target.valueAsNumber }))} />
        <small>{field.min}–4 · applied {simulation.config[field.key]}×</small>
      </label>)}</div>
      {configError === scope && <p className="simulation-config-error" role="alert">Enter each multiplier within its limits.</p>}
      <div className="simulation-settings-footer">
        <button type="button" onClick={() => { setConfigError(''); setConfig(current => { const next = { ...current }; for (const field of fields) next[field.key] = DEFAULT_CONFIG[field.key]; return next; }); }}>Default values</button>
        <button type="submit" disabled={conflict || !changed} className="simulation-apply">Apply changes</button>
      </div>
      <p className="simulation-draft-status">{changed ? 'Unapplied changes' : customized ? 'Custom settings active' : 'Reference defaults'} · affects future decisions</p>
    </form>;
  };
  return <div className="simulation-page" ref={page}>
    {renderMarket(<section ref={marketPanel} className={`simulation-market${expanded ? ' simulation-expanded' : ''}`} role={expanded ? 'dialog' : undefined} aria-modal={expanded ? true : undefined} aria-label="Simulated contract market">
      <header className="simulation-heading">
        <h1>Simulation <span title="Synthetic prices and participants. Isolated from accounts and live trading.">Synthetic market</span></h1>
        <div className="simulation-market-summary">
          <strong title={`Last executed trade · $${spec.tick.toFixed(2)} tick · $${spec.multiplier}/point`}>{instrument} <span>${price.toFixed(2)}</span></strong>
          <span title="Difference between best bid and ask">Spread <b>{spread === null ? '—' : '$' + spread.toFixed(2)}</b></span>
          <span title="Executed contracts, including warmup">Volume <b>{quantity(book.volume)}</b></span>
          <span className="simulation-regime" title={`${simulation.regime.name} · ${simulation.news}${newsActive ? ' · liquidity 60%' : ''}`}>{simulation.regime.name}</span>
        </div>
        <div className="simulation-heading-actions">
          <button aria-expanded={advancedOpen} aria-haspopup="dialog" onClick={() => setAdvancedOpen(true)}>Advanced settings</button>
          <button ref={expandButton} aria-label={expanded ? 'Exit expanded view' : 'Expand workspace'} aria-pressed={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? 'Exit' : 'Expand'}</button>
        </div>
      </header>
      <div className="simulation-toolbar" aria-label="Simulation controls">
        <button type="button" className="simulation-play" disabled={conflict || advancing !== null} onClick={() => dispatch({ type: 'toggle' })}>
          <span aria-hidden="true">{session.running ? 'Ⅱ' : '▶'}</span> {session.running ? 'Pause' : 'Resume'}
        </button>
        <button disabled={conflict} onClick={reset} title="Replace this run using the seed and applied settings; confirmation required">Reset</button>
        <label>Speed<select aria-label="Simulation speed" value={session.speed} onChange={e => dispatch({ type: 'speed', speed: Number(e.target.value) })}>
          {[0.5, 1, 2, 4].map(speed => <option key={speed} value={speed}>{speed}×</option>)}
        </select></label>
        <span className="simulation-toolbar-divider" />
        <label className="simulation-instrument-select"><span className="sr-only">Instrument</span><select aria-label="Simulation instrument" title={spec.name} value={instrument} onChange={e => { setJump(undefined); dispatch({ type: 'instrument', instrument: e.target.value as Instrument }); }}>
          {simulation.config.contractOrder.map(key => <option key={key} value={key}>{key} · {simulation.config.contracts[key].name}</option>)}
        </select></label>
        <label><span className="sr-only">Timeframe</span><select aria-label="Chart timeframe" value={session.timeframe} onChange={e => { setJump(undefined); dispatch({ type: 'timeframe', timeframe: e.target.value as Timeframe }); }}>
          {TIMEFRAMES.map(([tf]) => <option key={tf} value={tf}>{tf}</option>)}
        </select></label>
        <button className="simulation-sidebar-toggle" aria-expanded={sidebarOpen} aria-controls="simulation-side-panel" onClick={() => { setPanel('participants'); setSidebarOpen(!sidebarOpen || panel !== 'participants'); }}><Icon name="bot" /> Participants</button>
      </div>
      {conflict && <div className="simulation-confirm" role="alert" aria-label="Playback ownership">
        <p>Another simulation client owns this run. <strong>Use this tab</strong> takes over playback and loads the latest saved checkpoint here. Export first to keep this tab's unsaved state.</p>
        <button onClick={exportCheckpoint}>Export current run</button>
        <button onClick={runtime.retryOwnership}>Load saved run / retry ownership</button>
        <button onClick={runtime.takeOwnership}>Use this tab</button>
      </div>}

      <div className={`simulation-market-panels${sidebarOpen ? '' : ' simulation-sidebar-hidden'}`} style={{ '--simulation-side-width': (panel === 'book' ? bookWidth : 320) + 'px', '--simulation-chart-height': chartHeight + 'px' } as CSSProperties}>
        <div className="simulation-chart-panel">
          <SimulationChart key={`${session.generation}:${instrument}:${session.timeframe}`} runtime={runtime} symbol={instrument} tickSize={spec.tick} timeframe={session.timeframe} seconds={TIMEFRAMES.find(([tf]) => tf === session.timeframe)![1]} height={chartHeight} fitContainer jump={jump} />
        </div>
        {sidebarOpen && <aside id="simulation-side-panel" className="simulation-side-panel" aria-label="Market exploration">
          <nav className="simulation-panel-tabs" aria-label="Market panels">
            {(['participants', 'book', 'activity', 'statistics'] as const).map(key => <button key={key} aria-pressed={panel === key} onClick={() => setPanel(key)}>{({ participants: 'Participants', book: 'Book', activity: 'Activity', statistics: 'Stats' })[key]}</button>)}
            <button aria-label="Collapse side panel" title="Collapse side panel" onClick={() => setSidebarOpen(false)}>×</button>
          </nav>
          <div ref={sideContent} className="simulation-side-content">
            <section hidden={panel !== 'participants'} aria-label="Market participants" className="simulation-participants">
              <header><h2>Market participants</h2><p>{summaries.reduce((n, g) => n + g.count, 0)} traders · {simulation.participants.length} across all markets</p></header>
              {environmentControls(CONFIG_FIELDS.filter(f => f.key === 'liquidity' || f.key === 'whaleFrequency'))}
              <div className="simulation-participant-grid">
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
              </div><SimulationIndividuals runtime={runtime} instrument={instrument} active={panel === 'participants'} />

            </section>
            <div hidden={panel !== 'book'}><SimulationOrderBook key={`${session.generation}:${instrument}`} active={panel === 'book'} book={orders} lastPrice={price} tickSize={spec.tick} symbol={instrument} /></div>
            <div hidden={panel !== 'activity'}><SimulationActivity key={`${session.generation}:${instrument}`} active={panel === 'activity'} runtime={runtime} instrument={instrument} earliest={candles[0]?.time ?? Infinity} onJump={(time, label) => setJump({ time, label, id: (jump?.id ?? 0) + 1 })} /></div>
            <section hidden={panel !== 'statistics'} aria-label="Detailed statistics">      <dl className="simulation-metrics">
        <div><dt>Spread</dt><dd>{spread === null ? '—' : '$' + spread.toFixed(2)}</dd><span>{spread === null ? 'Waiting for two-sided quotes' : Math.round(spread / spec.tick) + ' ticks between bid & ask'}</span></div>
        <div><dt>Traded volume</dt><dd>{quantity(book.volume)} <small>contracts</small></dd><span>{instrument} executions, including warmup</span></div>
        <div><dt>Depth imbalance</dt><dd className={imbalance >= 0 ? 'simulation-up' : 'simulation-down'}>{depth ? Math.abs(imbalance).toFixed(0) + '% ' + (imbalance >= 0 ? 'bid' : 'ask') : '—'}</dd>
          <span>{quantity(market.bidContracts)} bid / {quantity(market.askContracts)} ask</span></div>
        <div><dt>Trade pressure <small>10s</small></dt><dd>{buy + sell ? buyPercent + '% buy' : '—'}</dd>
          <div className="simulation-pressure" aria-hidden="true"><i style={{ width: buyPercent + '%' }} /></div><span>{buy + sell ? (100 - buyPercent) + '% sell · aggressor volume' : 'Waiting for executions'}</span></div>
      </dl>
</section>
          </div>
        </aside>}
      </div>
      <footer className="simulation-status-strip">
        <span className="simulation-playback-state"><i className={session.running ? '' : 'is-paused'} />{advancing !== null ? 'Advancing' : session.running ? 'Running' : 'Paused'}</span>
        <span className="simulation-clock" aria-label="Simulation elapsed time">{simulationTime(simulation.time)}.{String(Math.round(simulation.time % 1 * 100)).padStart(2, '0')}</span>
        {advancing !== null ? <button onClick={runtime.cancelAdvance} title={`${Math.max(0, advancing - simulation.step).toLocaleString()} steps left`}>Cancel advancement</button> : <span className="simulation-status-news" title={simulation.news}>{simulation.news}</span>}
        <span className={savedLocally ? 'simulation-save-status' : 'simulation-save-status simulation-save-error'} title={savedLocally ? `Local save through ${simulationTime((savedStep ?? 0) * .25)}` : storageNotice}>{conflict ? 'Tab conflict · paused' : savedLocally ? 'Saved locally' : 'Memory only'}</span>
      </footer>
      {(storageNotice || notice) && <div className="simulation-notice"><p role={storageNotice ? 'alert' : 'status'}>{storageNotice || notice}</p>{notice && !storageNotice && <button aria-label="Dismiss notice" onClick={() => setNotice('')}>×</button>}</div>}
    </section>)}
    {resetPending && <SimulationDialog label="Replace simulation run" alert onClose={() => setResetPending(false)}>
      <div className="simulation-confirm"><p>Reset replaces all current prices, orders, participant positions, tape and saved continuation with seed <strong>{seed}</strong> and the applied environment settings. The opening auction and 30-minute warmup run again. Export first to keep this run.</p><button onClick={exportCheckpoint}>Export current run</button><button onClick={confirmReset}>Replace run</button><button autoFocus onClick={() => setResetPending(false)}>Cancel reset</button></div>
    </SimulationDialog>}
    {advancedOpen && <SimulationDialog label="Advanced settings" drawer onClose={() => setAdvancedOpen(false)}>
      <section className="simulation-advanced-section"><h3>Playback & reset</h3>
        <div className="simulation-activity-controls">
          <button disabled={session.running || conflict || advancing !== null} onClick={() => dispatch({ type: 'step', steps: 1 })}>Step 0.25s</button>
          {advancing !== null ? <button onClick={runtime.cancelAdvance}>Cancel advancement</button> : <button disabled={session.running || conflict} onClick={runtime.advanceBoundary}>Next {session.timeframe} boundary</button>}
        </div>
        <label className="simulation-seed">Random seed<input aria-label="Random seed" type="number" min="-2147483646" max="2147483646" step="1" required value={seed} onChange={e => setSeed(e.target.value)} /></label>
        <p>Used on the next reset. 1× = {BASE_SPEED} simulated seconds per second.</p>
        <p>{session.running ? 'Running' : 'Paused'} · seed {simulation.initialSeed} · step {simulation.step.toLocaleString()}</p>
      </section>
      <section className="simulation-advanced-section"><h3>Environment</h3>{environmentControls(CONFIG_FIELDS)}<p>Defaults are 1×. Changes affect future decisions and scheduling; existing positions and history are retained.</p></section>
      <details className="simulation-advanced-section"><summary>Display preferences</summary>
        <label>Small-screen chart height<input aria-label="Minimum chart height" type="range" min="300" max="720" step="10" value={chartHeight} onChange={e => setChartHeight(Number(e.target.value))} />{chartHeight}px</label>
        <label>Book width<input aria-label="Order book width" type="range" min="260" max="460" step="10" value={bookWidth} onChange={e => setBookWidth(Number(e.target.value))} />{bookWidth}px</label>
        <p>The desktop chart automatically fills the available height.</p>
      </details>
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

      </section>
      <details className="simulation-explainer"><summary>How this market works</summary>
        <p>All prices come from bot executions, matched by best price then arrival order at the resting price. Unfilled market quantity is cancelled; limit orders wait until filled, cancelled or expired. Bot stops and targets trade in the book. Margin and inventory limits can reject new exposure or force liquidation.</p>
        <p>Shared regimes and synthetic news influence fair value and participant decisions. They never set trade prices. Each candle opens at its first execution, with wicks and volume from that bucket only. Empty windows have no candle. All five timeframes are built independently from the same trades.</p>
        <p>All three markets run in order on one random stream. Switching views never changes that stream. The opening auction and 30-minute warmup use the same engine, followed by fixed 0.25-second steps at 18 simulated seconds per real second at 1× speed.</p>
        <p>The run and latest {MAX_CANDLES.toLocaleString()} candles per series are saved in this browser. Refresh and navigation keep the run; hidden or closed tabs do not accumulate catch-up work. Only Reset starts over. The same seed, configuration and steps reproduce the same sequence. No real orders are sent.</p>
      </details>

      {(storageNotice || notice) && <p role={storageNotice ? 'alert' : 'status'} className="simulation-feedback">{storageNotice || notice}</p>}
    </SimulationDialog>}
  </div>;
}
