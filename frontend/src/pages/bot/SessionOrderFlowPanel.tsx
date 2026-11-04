import { useEffect, useState } from "react";
import { boundedBotRequest } from "./botPageRequests";
import { timeLabel } from "./topBotPresentation";
import { usePageVisibility } from "./usePageVisibility";
import { coverageReasons, flowSummary, getSessionOrderFlow, reportedPressure, priceResponse, sessionKey, type FlowWindow, type SessionOrderFlow } from "./sessionOrderFlow";

const count = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 2 });
const percent = (n: number) => `${n.toFixed(1)}%`;

function VolumeReading({ value, muted }: { value: FlowWindow; muted: boolean }) {
  return <div className="session-flow-volume" data-muted={muted}>
    <strong>{reportedPressure(value)}</strong>
    {value.buy_percent != null ? <>
      <div className="session-flow-meter" aria-hidden="true"><i style={{ width: `${value.buy_percent}%` }} /></div>
      <p>{percent(value.buy_percent)} buy · {percent(100-value.buy_percent)} sell</p>
      <p>{count(value.buy)} buy / {count(value.sell)} sell contracts</p>
      <p>Reported delta <b>{value.delta! > 0 ? "+" : ""}{count(value.delta!)}</b> contracts</p>
    </> : <p>No buy/sell percentage available.</p>}
    <small>{count(value.unknown)} contracts unclassified{value.invalid ? ` · ${count(value.invalid)} invalid trades` : ""}</small>
    {value.classified_percent != null && <p>{percent(value.classified_percent)} of valid recorded volume classified</p>}
  </div>;
}

export function SessionOrderFlowPanel({ accountId, contractId, enabled, scope }: {
  accountId: number | null; contractId: string | null; enabled: boolean; scope: string | null;
}) {
  return <FlowPanel key={`${scope}:${accountId}:${contractId}:${enabled}`} accountId={accountId} contractId={contractId} enabled={enabled} />;
}

function FlowPanel({ accountId, contractId, enabled }: { accountId: number | null; contractId: string | null; enabled: boolean }) {
  const [flow, setFlow] = useState<SessionOrderFlow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [minutes, setMinutes] = useState<"5" | "15" | "60">("15");
  const [clock, setClock] = useState(Date.now);
  const visible = usePageVisibility();
  useEffect(() => {
    if (!enabled || !accountId || !contractId || !visible) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    const refresh = async () => {
      let delay = 30_000;
      controller = new AbortController();
      try {
        const result = await boundedBotRequest(getSessionOrderFlow(accountId, contractId, controller.signal), controller,
          "Session order flow timed out", 45_000);
        if (result.contract_id !== contractId || result.data_live !== false || result.schema !== "session-provider-flow-v1") throw new Error("Different market scope returned");
        if (!stopped) { setFlow(result); setError(null); setClock(Date.now()); }
        if (result.coverage.status === "building") delay = 1500;
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e.message : "Session order flow is unavailable");
      }
      if (!stopped) timer = setTimeout(refresh, delay);
    };
    void refresh();
    const tick = setInterval(() => setClock(Date.now()), 15_000);
    return () => { stopped = true; clearTimeout(timer); clearInterval(tick); controller?.abort(); };
  }, [accountId, contractId, enabled, visible]);
  return <SessionOrderFlowView {...{ flow, error, minutes, setMinutes, clock, accountId, contractId, enabled }} />;
}

export function SessionOrderFlowView({ flow, error, minutes, setMinutes, clock, accountId, contractId, enabled }: {
  flow: SessionOrderFlow | null; error: string | null; minutes: "5" | "15" | "60";
  setMinutes: (value: "5" | "15" | "60") => void; clock: number;
  accountId: number | null; contractId: string | null; enabled: boolean;
}) {
  const aged = flow ? clock-Date.parse(flow.generated_at) > 120_000 : false;
  const previousSession = flow ? sessionKey(clock) !== sessionKey(Date.parse(flow.session_start)) : false;
  const muted = Boolean(error || aged || previousSession || flow?.coverage.stale || flow?.coverage.status === "building");
  const recent = flow?.windows[minutes];
  return <section className="topbot-panel session-flow" aria-label="Session order flow">
    <div className="topbot-section-heading"><div><h2>Session order flow</h2>
      <p>Recorded market trades · session starts at 6 p.m. New York time</p></div>
      <span>{flow ? flow.coverage.status === "building" ? "Building history" : muted ? "Stale / unavailable" : flow.coverage.status === "partial" ? "Partial coverage" : "Observed coverage" : "Unavailable"}</span>
    </div>
    <p className="session-flow-summary">{!enabled ? "Live context paused." : !accountId ? "Select a connected account." : !contractId ? "Waiting for a market." : previousSession ? "New session started. Awaiting fresh session recordings." : error ? "Session order flow is unavailable." : aged ? "Last reading is stale. Awaiting fresh recordings." : flow ? flowSummary(flow) : "Checking recorded trades…"}</p>
    {error && <p className="topbot-warning session-flow-note" role="status">{error}</p>}
    <p className="session-flow-note">Bullish or bearish pressure describes the balance of ProjectX Buy/Sell-labeled contracts in the available recording. It is not a price forecast or confirmation of institutional activity.</p>
    <div className="session-flow-grid">
      <article><h3>{previousSession ? "Previous session volume" : flow?.coverage.reasons.session_start_missing ? "Recorded session volume" : "Since session open"}</h3>
        {flow ? <VolumeReading value={flow.session} muted={muted} /> : <strong>Awaiting recorded volume</strong>}
      </article>
      <article><div className="session-flow-window"><h3>Recent pressure</h3>
        <select aria-label="Recent order-flow window" value={minutes} onChange={e => setMinutes(e.target.value as "5" | "15" | "60")}>
          <option value="5">Last 5 min</option><option value="15">Last 15 min</option><option value="60">Last 60 min</option>
        </select></div>
        {flow ? <><VolumeReading value={flow.windows[minutes]} muted={muted} />
          <small>{timeLabel(flow.windows[minutes].start)} – {timeLabel(flow.windows[minutes].end)}</small></> : <strong>Awaiting recorded volume</strong>}
      </article>
      <article><h3>Price response · last {minutes} min</h3><strong>{recent?.price_change != null
        ? `Observed price ${recent.price_change > 0 ? "rose" : recent.price_change < 0 ? "fell" : "held"}${recent.price_change ? ` ${count(Math.abs(recent.price_change))} pts` : ""}` : "Awaiting price observations"}</strong>
        {recent?.first_price_at && recent.last_price_at && <p>First to last recorded trade: {timeLabel(recent.first_price_at)} – {timeLabel(recent.last_price_at)}</p>}
        <p>{recent && !muted ? priceResponse(recent) : "Current flow/price comparison is unavailable."}</p>
        <p>Flow/price divergence alone does not confirm absorption.</p>
        <p>Price movement does not establish which side caused it.</p>
      </article>
    </div>
    {flow && <div className="session-flow-coverage">
      <p>Session opened {timeLabel(flow.session_start)} · totals through {timeLabel(flow.through)} · {flow.source}</p>
      {flow.coverage.first_received_at && <p>Available recording starts {timeLabel(flow.coverage.first_received_at)}.</p>}
      <p>{flow.coverage.status === "building" ? `${flow.coverage.pending_segments} segments still to process. ` : ""}Recorded deliveries only; full exchange coverage is not certified. Latest unsealed trades are not included.</p>
      {Object.keys(flow.coverage.reasons).length > 0 && <ul>{Object.keys(flow.coverage.reasons).map(reason => <li key={reason}>{coverageReasons[reason] ?? "Recording quality issue"}</li>)}</ul>}
    </div>}
    <details className="session-flow-method"><summary>How to read these numbers</summary>
      <p>Reported delta is Buy-labeled volume minus Sell-labeled volume. Percentages use only classified contracts; they are not probabilities. Unknown sides and unreliable timestamps stay unclassified. Counts use complete recorded minutes, so recent windows end at the displayed “totals through” time.</p>
      <p>Each executed trade has a buyer and seller. A provider trade label is different from resting bids and asks in the order book. Replayed provider deliveries cannot be reliably identified without exchange trade IDs.</p>
      <p>The Buy/Sell mapping was cross-checked against Quantower's provider-mode aggressor labels in a 60-second MNQ December 2026 sample on November 3, 2026. Both platforms use ProjectX; this is a sample consistency check, not independent exchange verification or certification of the current session.</p>
      <p>Pressure is uncertain when unclassified contracts could erase or reverse the measured delta, or invalid trades leave their volume unknown. Positive delta means bullish reported pressure; negative delta means bearish reported pressure. Its size is shown directly without borrowing the bot's DOM thresholds.</p>
      <a href="https://gateway.docs.projectx.com/docs/realtime/" target="_blank" rel="noreferrer">ProjectX trade definitions</a>
    </details>
  </section>;
}
