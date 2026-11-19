import { useState, type FormEvent } from "react";
import { botsApi } from "../../lib/api";
import type { BotConfig } from "../../lib/types";

const fields = [
  ["contracts", "Contracts (MNQ)", 1],
  ["daily_loss_limit", "Daily loss limit ($)", 0.01],
  ["daily_profit_target", "Daily profit target ($)", 0.01],
  ["take_profit_points", "Take-profit (points)", 0.25],
  ["stop_loss_points", "Stop-loss (points)", 0.25],
] as const;

function values(bot: BotConfig) {
  return {
    contracts: String(bot.order_size),
    daily_loss_limit: String(bot.max_daily_loss),
    daily_profit_target: String(bot.strategy_params.daily_pnl_limit ?? 500),
    take_profit_points: String(bot.strategy_params.take_profit_points ?? 25),
    stop_loss_points: String(bot.strategy_params.initial_stop_points ?? 25),
    expected_updated_at: bot.updated_at,
  };
}

export function OrderflowSettings({ bot, onChanged }: { bot: BotConfig; onChanged: () => void }) {
  const [saved, setSaved] = useState<BotConfig | null>(null);
  const [draft, setDraft] = useState<ReturnType<typeof values> | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const current = saved && Date.parse(saved.updated_at) > Date.parse(bot.updated_at) ? saved : bot;
  const form = draft ?? values(current);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft || busy) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await botsApi.saveOrderflowSettings(bot.id, {
        expected_updated_at: draft.expected_updated_at, contracts: Number(draft.contracts),
        daily_loss_limit: Number(draft.daily_loss_limit), daily_profit_target: Number(draft.daily_profit_target),
        take_profit_points: Number(draft.take_profit_points), stop_loss_points: Number(draft.stop_loss_points),
      });
      setSaved(result);
      setDraft(null);
      setMessage("Settings saved.");
      onChanged();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save settings.");
      onChanged();
    } finally { setBusy(false); }
  }
  return <section className="topbot-panel" aria-labelledby="orderflow-title">
    <h2 id="orderflow-title" className="text-base font-semibold">Orderflow Imbalance</h2>
    <p className="topbot-muted">{current.strategy_params.imbalance_direction === "following" ? "Bid-heavy → long · Ask-heavy → short" : "Bid-heavy → short · Ask-heavy → long"}
      {" · "}{Number(current.strategy_params.imbalance_threshold ?? 0.1) * 100}% imbalance for {Number(current.strategy_params.persistence_seconds ?? 30)} seconds</p>
    <form onSubmit={save}>
      <div className="topbot-comparison-filters">
        {fields.map(([key, label, step]) => <label key={key}>{label}
          <input type="number" required disabled={busy} min={step} step={step} max={key === "contracts" ? 2 : undefined}
            value={form[key]} onChange={e => { setDraft({ ...form, [key]: e.target.value }); setMessage(""); }} />
        </label>)}
      </div>
      <p className="topbot-muted">Size and bracket apply to new entries. Daily limits apply to this practice account’s current trading session.</p>
      <button className="topbot-run-button" type="submit" disabled={!draft || busy}>{busy ? "Saving…" : "Save settings"}</button>
      {draft && <button className="topbot-run-button" type="button" disabled={busy} onClick={() => { setDraft(null); setMessage(""); }}>Discard edits</button>}
      {message && <span className="topbot-muted" role="status"> {message}</span>}
    </form>
  </section>;
}
