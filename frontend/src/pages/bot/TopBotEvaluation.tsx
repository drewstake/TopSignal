import { useMemo, type ReactNode } from "react";
import type { BotActivity } from "../../lib/types";
import { buildMarketContext, candleEndMs, type BotMarketSnapshot } from "./botMarketContext";
import { buildMarketExplanation } from "./botMarketExplanation";
import { decisionIsStale, latestDecision, priceLabel, recordedEvents, timeLabel } from "./topBotPresentation";

export function TopBotThinking({ snapshot, now, liveImbalance }: {
  snapshot: BotMarketSnapshot | null; now: number; liveImbalance?: ReactNode;
}) {
  const context = useMemo(() => buildMarketContext(snapshot, now), [snapshot, now]);
  const explanation = context && context.provenance.closedCandleCount >= 25 ? buildMarketExplanation(context) : null;
  return <section className="topbot-panel" aria-label="Chart context">
    {liveImbalance}
    <div className="topbot-context-divider"><span>LIVE CHART CONTEXT</span><span>{context?.provenance.isStale ? "Stale candles" : "Completed candles"}</span></div>
    {explanation ? <>
      <h3>{explanation.headline}</h3><p className="topbot-muted">{explanation.summary}</p>
      <div className="topbot-evidence"><Evidence title="Supporting context" entries={explanation.supporting} /><Evidence title="Conflicting context" entries={explanation.conflicting} /></div>
      <p className="topbot-footnote">Derived from the chart; not a new bot evaluation. {context?.asOfTimestamp && snapshot ? `Candle closed ${timeLabel(new Date(candleEndMs(Date.parse(context.asOfTimestamp), snapshot.unit, snapshot.unitNumber)).toISOString())}.` : ""}</p>
    </> : <p className="topbot-muted">Waiting for enough completed chart candles to describe current conditions.</p>}
  </section>;
}

function Evidence({ title, entries }: { title: string; entries: string[] }) {
  return <div><h4>{title}</h4>{entries.length ? <ul>{entries.slice(0, 2).map(entry => <li key={entry}>{entry}</li>)}</ul> : <p>No evidence reported.</p>}</div>;
}

export function TopBotEvaluation({ activity, now, failed, strategyUnconfigured = false }: { activity: BotActivity | null; now: number; failed: boolean; strategyUnconfigured?: boolean }) {
  const decision = latestDecision(activity);
  const stale = decision && activity ? decisionIsStale(decision, activity, now) : false;
  const attempt = activity?.order_attempts.find(row => row.bot_decision_id === decision?.id);
  const risks = decision ? activity?.risk_events.filter(row => row.bot_run_id === decision.bot_run_id && row.bot_run_id !== null
    && Math.abs(Date.parse(row.created_at) - Date.parse(decision.created_at)) < 60_000) ?? [] : [];
  const action = strategyUnconfigured ? "NO STRATEGY" : !decision ? "AWAITING EVALUATION" : decision.action === "HOLD" ? "NO TRADE" : decision.action;
  const raw = decision?.raw_payload;
  return <section className="topbot-panel" aria-label="Latest evaluation">
    <div className="topbot-section-heading"><h2>Latest evaluation</h2><span className={stale || failed ? "topbot-warning" : ""}>{decision ? stale || failed ? "Stale · recorded" : "Recorded" : "Not available"}</span></div>
    <div className="topbot-decision" data-tone={decision?.action === "BUY" ? "bullish" : decision?.action === "SELL" ? "bearish" : "neutral"}>
      <strong>{action}</strong><time>{decision ? timeLabel(decision.created_at) : "No recorded decision"}</time>
    </div>
    <p className="topbot-main-reason">{strategyUnconfigured ? "Evaluations will begin after you define a strategy." : decision?.reason ?? "The bot’s next recorded decision and explanation will appear here."}</p>
    <dl className="topbot-levels">{[["Entry", raw?.entry_price], ["Stop", raw?.stop_loss], ["Target", raw?.take_profit]].map(([label, value]) => <div key={String(label)}><dt>{String(label)}</dt><dd>{priceLabel(value)}</dd></div>)}</dl>
    <p className="topbot-footnote">Levels appear only when explicitly reported. A recorded signal does not confirm an order or fill.</p>
    <div className="topbot-context-divider"><span>EXECUTION & RISK CHECKS</span></div>
    <p className="topbot-muted">{attempt ? `Order: ${attempt.status.replaceAll("_", " ")}${attempt.rejection_reason ? ` · ${attempt.rejection_reason}` : ""}` : "No order linked to this evaluation."}</p>
    {risks.length > 0 && <ul className="topbot-risk-list">{risks.map(risk => <li key={risk.id}>{risk.message}</li>)}</ul>}
    <p className="topbot-footnote">Detailed strategy evidence and entry conditions are unavailable unless included in the recorded reason.</p>
  </section>;
}

export function TopBotActivity({ activity }: { activity: BotActivity | null }) {
  const events = recordedEvents(activity);
  return <section className="topbot-panel" aria-label="Recent bot activity">
    <div className="topbot-section-heading"><h2>Recent activity</h2><span>Decisions · orders · risk</span></div>
    {events.length ? <ol className="topbot-event-list">{events.map(event => <li key={event.key}>
      <span className="topbot-event-dot" /><div><div className="topbot-event-heading"><strong>{event.kind}</strong><span>{event.label}</span><time>{timeLabel(event.at)}</time></div><p>{event.text}</p></div>
    </li>)}</ol> : <p className="topbot-muted">No activity yet.</p>}
  </section>;
}
