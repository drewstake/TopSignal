import { useEffect, useRef, useState } from "react";
import { type SimulationCandle } from "./simulationPreview";
import { useSmoothedCandle } from "./useSmoothedCandle";

const money = (value: number) => value.toFixed(2);

export function SimulationChart({ candles }: {
  candles: SimulationCandle[];
}) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(960);
  const [selectedUpdate, setSelectedUpdate] = useState<number | null>(null);
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);

  const plotLeft = 14;
  const plotRight = width - 66;
  const plotTop = 24;
  const plotBottom = 322;
  const visible = candles.slice(-Math.max(20, Math.floor((plotRight - plotLeft) / 10)));
  const low = Math.min(...visible.map((bar) => bar.low));
  const high = Math.max(...visible.map((bar) => bar.high));
  const padding = Math.max((high - low) * 0.16, 0.02);
  const minimum = low - padding;
  const maximum = high + padding;
  const spacing = Math.min(12, (plotRight - plotLeft) / visible.length);
  const x = (index: number) => plotLeft + (index + 0.5) * spacing;
  const y = (price: number) => plotTop + (maximum - price) / (maximum - minimum) * (plotBottom - plotTop);
  const last = visible[visible.length - 1];
  const displayClose = useSmoothedCandle(last);
  const selectedIndex = visible.findIndex((bar) => bar.update === selectedUpdate);
  const selected = visible[selectedIndex] ?? last;
  const maxVolume = Math.max(1, ...visible.map((bar) => bar.volume));
  // Match direction to the displayed cent, avoiding sub-cent red/green flicker.
  const positive = Math.round(displayClose * 100) >= Math.round(last.open * 100);
  const candleWidth = spacing;
  const updateLabelEvery = Math.max(1, Math.ceil(visible.length / Math.max(2, Math.floor(width / 125))));

  return <div className="simulation-chart-wrap" ref={container}>
    <div className="simulation-ohlc" aria-live="off">
      <span>Candle {selected.update}</span>
      {([['O', selected.open], ['H', selected.high], ['L', selected.low], ['C', selected.close]] as const).map(([label, value]) =>
        <span key={label}>{label} <strong>{money(value)}</strong></span>)}
      <span>Vol <strong>{selected.volume < 1000 ? selected.volume : `${(selected.volume / 1000).toFixed(1)}K`}</strong></span>
      <span title="The live candle and ~ price marker are smoothed. OHLC values and completed candles show actual trades.">Live motion smoothed · OHLC exact</span>
    </div>
    <svg className="simulation-chart" viewBox={`0 0 ${width} 440`} role="img" tabIndex={0}
      aria-label={`Simulated BLOX100 candlestick chart with volume. Last trade price $${money(last.close)}. Use left and right arrow keys to inspect candles.`}
      onPointerMove={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect();
        const pointerX = (event.clientX - bounds.left) * width / bounds.width;
        const index = Math.max(0, Math.min(visible.length - 1, Math.floor((pointerX - plotLeft) / spacing)));
        setSelectedUpdate(visible[index].update);
      }}
      onPointerLeave={() => setSelectedUpdate(null)}
      onBlur={() => setSelectedUpdate(null)}
      onKeyDown={(event) => {
        if (event.key === 'Escape') setSelectedUpdate(null);
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        const current = selectedIndex < 0 ? visible.length - 1 : selectedIndex;
        setSelectedUpdate(visible[Math.max(0, Math.min(visible.length - 1, current + (event.key === 'ArrowLeft' ? -1 : 1)))].update);
      }}>
      <title>BLOX100 trade prices and traded volume</title>
      {Array.from({ length: 6 }, (_, index) => {
        const price = maximum - index / 5 * (maximum - minimum);
        return <g key={index} className="simulation-grid">
          <line x1={plotLeft} x2={plotRight} y1={y(price)} y2={y(price)} />
          <text x={plotRight + 12} y={y(price) + 4}>{money(price)}</text>
        </g>;
      })}
      {visible.map((bar, index) => index % updateLabelEvery === 0 && <g key={bar.update} className="simulation-grid">
        <line x1={x(index)} x2={x(index)} y1={plotTop} y2={407} />
      </g>)}
      <text x={plotLeft + 8} y={plotTop + 30} className="simulation-watermark">BLOX100 / SIM</text>
      {visible.map((bar, index) => {
        const close = bar.update === last.update ? displayClose : bar.close;
        const up = bar.update === last.update ? positive : bar.close >= bar.open;
        return <g key={bar.update} className={up ? 'simulation-up' : 'simulation-down'}>
          <rect className="simulation-candle-shape" x={x(index) - 0.5} y={y(bar.high)}
            width={1} height={Math.max(1, y(bar.low) - y(bar.high))} fill="currentColor" />
          <rect className="simulation-candle-body" data-candle={bar.update} data-display-close={close} x={x(index) - candleWidth / 2} y={Math.min(y(bar.open), y(close))}
            width={candleWidth} height={Math.max(1.5, Math.abs(y(close) - y(bar.open)))} fill="currentColor" />
        </g>;
      })}
      <g className={`simulation-price-marker ${positive ? 'simulation-up' : 'simulation-down'}`} style={{ transform: `translateY(${y(displayClose)}px)` }}>
        <title>Smoothed live price. Actual last trade ${money(last.close)}.</title>
        <line x1={plotLeft} x2={plotRight} y1={0} y2={0} stroke="currentColor" strokeDasharray="4 4" opacity={0.6} />
        <rect x={plotRight + 3} y={-11} width={61} height={22} rx={4} fill="currentColor" />
        <text x={plotRight + 33} y={4} textAnchor="middle" className="simulation-price-label">~{money(displayClose)}</text>
      </g>
      <line className="simulation-divider" x1={plotLeft} x2={plotRight} y1={345} y2={345} />
      <text x={plotLeft} y={362} className="simulation-axis-label">VOLUME</text>
      {visible.map((bar, index) => {
        const height = bar.volume / maxVolume * 38;
        const up = bar.update === last.update ? positive : bar.close >= bar.open;
        return <rect key={bar.update} x={x(index) - candleWidth / 2} y={407 - height} width={candleWidth} height={height}
          className={`simulation-candle-shape ${up ? 'simulation-up' : 'simulation-down'}`} fill="currentColor" opacity={0.4} rx={1} />;
      })}
      {selectedIndex >= 0 && <g className="simulation-crosshair">
        <line x1={x(selectedIndex)} x2={x(selectedIndex)} y1={plotTop} y2={407} />
        <line x1={plotLeft} x2={plotRight} y1={y(selected.close)} y2={y(selected.close)} />
        <circle cx={x(selectedIndex)} cy={y(selected.close)} r={4} />
      </g>}
    </svg>
    <span className="sr-only" role="status">{selectedIndex >= 0 ? `Candle ${selected.update}. Open ${money(selected.open)}, high ${money(selected.high)}, low ${money(selected.low)}, close ${money(selected.close)}.` : ''}</span>
  </div>;
}
