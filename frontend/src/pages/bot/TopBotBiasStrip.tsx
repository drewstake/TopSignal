import { useEffect, useMemo, useRef, useState } from "react";
import { botsApi, getAuthenticatedCacheScope } from "../../lib/api";
import type { ProjectXMarketCandle } from "../../lib/types";
import { BOT_CHART_TIMEFRAMES, type BotChartMarket } from "./botChartData";
import { buildMarketContext, candleEndMs } from "./botMarketContext";
import { intervalSecondsFor } from "./botCandleGaps";
import { boundedBotRequest } from "./botPageRequests";
import { timeLabel } from "./topBotPresentation";
import { usePageVisibility } from "./usePageVisibility";
import { buildBotCandleCacheKey, readBotCandleCache, writeBotCandleCache } from "./botCandleCache";

const frames = [...BOT_CHART_TIMEFRAMES].reverse();
type FrameData = { rows: ProjectXMarketCandle[]; failed: boolean; checkedAt?: number };
type BiasProps = { market: BotChartMarket | null; demoMode: boolean };
const refreshInterval = 60_000;
const cacheKey = (scope: string, contract: string, symbol: string | null | undefined, frame: typeof frames[number]) =>
  buildBotCandleCacheKey({ userScope: `${scope}:topbot-bias`, contractId: contract, symbol, live: false, unit: frame.unit, unitNumber: frame.unitNumber });

export function TopBotBiasStrip(props: BiasProps) {
  const [scope, setScope] = useState<string | null>(null);
  useEffect(() => {
    if (props.demoMode) return;
    let disposed = false;
    void getAuthenticatedCacheScope().then(value => { if (!disposed) setScope(value); }).catch(() => {
      if (!disposed) setScope(""); // Network-only fallback when authentication storage is unavailable.
    });
    return () => { disposed = true; };
  }, [props.demoMode]);
  return <BiasReadings key={`${props.demoMode}:${scope}:${props.market?.contract_id}:${props.market?.symbol}`}
    {...props} scope={props.demoMode ? null : scope} />;
}

function BiasReadings({ market, demoMode, scope }: BiasProps & { scope: string | null }) {
  const [data, setData] = useState<Record<string, FrameData>>(() => {
    if (!scope || !market || demoMode) return {};
    return Object.fromEntries(frames.flatMap(frame => {
      const cached = readBotCandleCache(cacheKey(scope, market.contract_id, market.symbol, frame));
      const rows = cached?.candles.filter(row => row.contract_id === market.contract_id && row.unit === frame.unit && row.unit_number === frame.unitNumber);
      return rows?.length ? [[frame.id, { rows: rows.slice(-80), failed: false, checkedAt: cached?.savedAt?.getTime() }]] : [];
    }));
  });
  const [now, setNow] = useState(Date.now);
  const dataRef = useRef(data);
  useEffect(() => { dataRef.current = data; }, [data]);
  const visible = usePageVisibility();
  const contract = market?.contract_id;
  const symbol = market?.symbol;
  useEffect(() => {
    if (!contract || demoMode || !visible || scope === null) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    // Preserve cached readings while refreshing; a remount must not refetch
    // frames that were just checked. Candle timestamps still determine staleness.
    const checkedAt = Object.fromEntries(Object.entries(dataRef.current).map(([id, entry]) => [id, entry.checkedAt ?? 0]));
    const refresh = async () => {
      // Six bounded 80-bar reads, sequential to avoid competing with the live chart.
      for (const frame of frames) {
        if (stopped) return;
        const age = Date.now() - (checkedAt[frame.id] ?? 0);
        if (age >= 0 && age < refreshInterval) continue;
        controller = new AbortController();
        const end = new Date();
        const span = Math.max(5 * 86400, intervalSecondsFor(frame.unit, frame.unitNumber) * 80 * 3);
        try {
          const rows = await boundedBotRequest(botsApi.getCandles({ contractId: contract, symbol: symbol ?? undefined,
            unit: frame.unit, unitNumber: frame.unitNumber, limit: 80, live: false, includePartialBar: false,
            start: new Date(end.getTime() - span * 1000).toISOString(), end: end.toISOString(),
          }, { signal: controller.signal }), controller, "Timeframe data unavailable", 15_000);
          if (!stopped) {
            checkedAt[frame.id] = Date.now();
            if (scope && rows.length) writeBotCandleCache(cacheKey(scope, contract, symbol, frame), rows, 80);
            setData(previous => ({ ...previous, [frame.id]: { rows, failed: false, checkedAt: checkedAt[frame.id] } }));
          }
        } catch {
          if (!stopped) setData(previous => ({ ...previous, [frame.id]: { rows: previous[frame.id]?.rows ?? [], failed: true } }));
        }
      }
      if (!stopped) { setNow(Date.now()); timer = setTimeout(refresh, refreshInterval); }
    };
    void refresh();
    const clock = setInterval(() => setNow(Date.now()), 15_000);
    return () => { stopped = true; clearTimeout(timer); clearInterval(clock); controller?.abort(); };
  }, [contract, symbol, demoMode, visible, scope]);

  const readings = useMemo(() => frames.map(frame => {
    const entry = data[frame.id];
    const context = entry && contract ? buildMarketContext({ contractKey: `${contract}:${frame.unit}:${frame.unitNumber}`,
      unit: frame.unit, unitNumber: frame.unitNumber, candles: entry.rows, lastPrice: null, updatedAt: new Date(now).toISOString(),
    }, now) : null;
    const trend = context?.trend;
    const stale = context?.provenance.isStale || entry?.failed;
    const close = context?.asOfTimestamp ? new Date(candleEndMs(Date.parse(context.asOfTimestamp), frame.unit, frame.unitNumber)).toISOString() : null;
    return { frame, context, trend, stale, close, label: trend ? trend.direction : entry || demoMode ? "unavailable" : "loading" };
  }), [contract, data, demoMode, now]);
  const fresh = readings.filter(row => row.trend && !row.stale);
  const bullish = fresh.filter(row => row.trend?.direction === "bullish").length;
  const bearish = fresh.filter(row => row.trend?.direction === "bearish").length;
  const alignment = !fresh.length ? "Waiting for timeframe data" : bullish && bearish ? "Timeframes are mixed"
    : bullish ? `${bullish} bullish · ${fresh.length - bullish} neutral` : bearish ? `${bearish} bearish · ${fresh.length - bearish} neutral` : "No directional agreement";

  return <section className="topbot-bias" aria-label="Timeframe biases">
    <div className="topbot-section-heading"><div><h2>Market bias</h2><p>Candle context · separate from the bot’s recorded decision</p></div><span>{alignment}</span></div>
    <div className="topbot-bias-grid">{readings.map(({ frame, trend, stale, close, label, context }) =>
      <article key={frame.id} className="topbot-bias-cell" data-tone={stale ? "warning" : label}>
        <div className="topbot-bias-label"><span>{frame.id === "1d" ? "DAILY" : frame.label.toUpperCase()}</span><span aria-hidden="true">{trend?.direction === "bullish" ? "↗" : trend?.direction === "bearish" ? "↘" : "—"}</span></div>
        <strong>{label === "loading" ? "Checking…" : label.charAt(0).toUpperCase() + label.slice(1)}</strong>
        <p>{stale ? "Stale · awaiting fresh data" : trend ? trend.direction === "neutral" ? "No clear trend in closed bars" : "EMA spread + slope agree" : demoMode ? "Live data paused in demo" : "Needs 25 completed candles"}</p>
        <time title={timeLabel(close)}>{close ? timeLabel(close).replace(/:\d{2} (?=[AP]M)/, " ") : "Close time unavailable"}</time>
        <div className="topbot-bias-rule" title={`${context?.provenance.closedCandleCount ?? 0} completed candles · EMA 9/21 trend, not a forecast probability`} />
      </article>,
    )}</div>
  </section>;
}
