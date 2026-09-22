import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, botsApi } from "../../lib/api";
import type { ManualOrderInput, ManualOrderResult, ManualOrderState } from "../../lib/manualOrderTypes";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/Card";

interface Props {
  accountId: number;
  accountName: string;
  disabledReason: string | null;
  demoMode: boolean;
  onFlatten: () => void;
  flattening: boolean;
}

function orderStatus(result: ManualOrderResult) {
  const labels: Record<string, string> = {
    working: "Working", filled: "Entry filled", partially_filled: "Entry partially filled",
    take_profit_hit: "Take profit hit · trade closed", stop_loss_hit: "Stop loss hit · trade closed",
    closed: "Trade closed", partially_closed: "Trade partially closed", cancelled: "Entry cancelled",
    expired: "Entry expired", rejected: "Rejected", flat_exit_unconfirmed: "Entry filled · account flat; exit not identified",
  };
  return result.execution_status ? labels[result.execution_status] ?? result.execution_status :
    result.status === "error" ? "Rejected" : result.status.replaceAll("_", " ");
}

function fillDetails(result: ManualOrderResult) {
  return `${result.entry_fill_price != null ? ` · Entry ${result.entry_fill_price}` : ""}${result.exit_fill_price != null ? ` · Exit ${result.exit_fill_price}` : ""}`;
}

export function ManualOrderPanel({ accountId, accountName, disabledReason, demoMode, onFlatten, flattening }: Props) {
  const [snapshot, setSnapshot] = useState<ManualOrderState | null>(null);
  const [quantity, setQuantity] = useState("1");
  const [stop, setStop] = useState("20");
  const [target, setTarget] = useState("40");
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ManualOrderResult | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const mounted = useRef(true);
  const sending = useRef(false);
  const recoveryRequest = useRef<ManualOrderInput | null>(null);
  const reader = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    if (demoMode || reader.current) return;
    const controller = new AbortController();
    reader.current = controller;
    setRefreshing(true);
    try {
      const state = await botsApi.getManualOrderState(accountId, { signal: controller.signal });
      if (!mounted.current || controller.signal.aborted) return;
      if (state.account_id !== accountId) throw new Error("Broker response did not match this account.");
      setSnapshot(state);
      setError(null);
      const recovered = recoveryRequest.current
        ? state.recent_attempts.find((a) => a.request_id === recoveryRequest.current?.request_id)
        : state.recent_attempts[0];
      if (recovered) {
        setResult(recovered);
        setUncertain(["pending", "submission_unknown"].includes(recovered.status));
      }
    } catch (err) {
      if (mounted.current && !controller.signal.aborted) setError(err instanceof Error ? err.message : "Could not read broker orders.");
    } finally {
      if (reader.current === controller) reader.current = null;
      if (mounted.current) setRefreshing(false);
    }
  }, [accountId, demoMode]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => { mounted.current = false; reader.current?.abort(); reader.current = null; };
  }, [refresh]);
  const hasExposure = Boolean(snapshot?.positions.length || snapshot?.orders.length);
  useEffect(() => {
    if (!hasExposure && !uncertain && result?.status !== "submitted") return;
    const settleUntil = Date.now() + 30_000;
    const interval = window.setInterval(() => {
      if (!hasExposure && !uncertain && Date.now() >= settleUntil) { window.clearInterval(interval); return; }
      if (!document.hidden) void refresh();
    }, 5_000);
    return () => window.clearInterval(interval);
  }, [hasExposure, uncertain, result?.status, refresh]);

  const size = Number(quantity), sl = Number(stop), tp = Number(target);
  const valid = Number.isInteger(size) && size >= 1 && size <= 10 &&
    Number.isInteger(sl) && sl >= 1 && sl <= 1000 && Number.isInteger(tp) && tp >= 1 && tp <= 1000;
  const risk = snapshot ? size * sl * snapshot.contract.tick_value : 0;
  const unresolved = snapshot?.recent_attempts.some((a) => ["pending", "submission_unknown"].includes(a.status));
  const blocked = demoMode || Boolean(disabledReason) || !snapshot || !valid ||
    risk > (snapshot?.max_stop_risk ?? 250) || hasExposure || unresolved || uncertain || busy || flattening;

  async function send(payload: ManualOrderInput) {
    if (sending.current) return;
    sending.current = true;
    recoveryRequest.current = payload;
    setBusy(true); setError(null); setResult(null);
    try {
      const response = await botsApi.submitManualOrder(accountId, payload);
      if (!mounted.current) return;
      if (response.account_id !== accountId || response.request_id !== payload.request_id) throw new Error("Order response did not match this request.");
      setResult(response);
      setUncertain(["pending", "submission_unknown"].includes(response.status));
      void refresh();
    } catch (err) {
      if (!mounted.current) return;
      const rejectedBeforeSubmission = err instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(err.status);
      setUncertain(!rejectedBeforeSubmission);
      if (rejectedBeforeSubmission) recoveryRequest.current = null;
      setError(err instanceof Error ? err.message : "Submission response unavailable.");
    } finally {
      sending.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  function submit(side: "BUY" | "SELL") {
    if (blocked || sending.current) return;
    if (!window.confirm(`${side} ${size} MNQ at market on ${accountName} (${accountId})?\nStop loss: ${sl} ticks. Take profit: ${tp} ticks.\nThis sends an actual order to TopstepX.`)) return;
    void send({ request_id: crypto.randomUUID(), side, quantity: size, stop_loss_ticks: sl,
      take_profit_ticks: tp, confirm_live_order_routing: true });
  }

  return <Card className="p-3 md:p-3">
    <CardHeader className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 space-y-0">
      <CardTitle>Manual order test</CardTitle>
      <p className="text-xs text-app-muted">{accountName} ({accountId})</p>
    </CardHeader>
    <CardContent className="space-y-2">
      <div className="grid items-end gap-3 lg:grid-cols-3">
        <div className="grid grid-cols-3 gap-2">
          <label className="space-y-1 text-xs text-app-muted">Contracts<Input className="h-8 rounded-lg px-2" aria-label="Test contracts" type="number" min={1} max={10} step={1} value={quantity} disabled={busy || uncertain} onChange={(e) => setQuantity(e.target.value)} /></label>
          <label className="space-y-1 text-xs text-app-muted">SL ticks<Input className="h-8 rounded-lg px-2" aria-label="Test stop loss ticks" type="number" min={1} max={1000} step={1} value={stop} disabled={busy || uncertain} onChange={(e) => setStop(e.target.value)} /></label>
          <label className="space-y-1 text-xs text-app-muted">TP ticks<Input className="h-8 rounded-lg px-2" aria-label="Test take profit ticks" type="number" min={1} max={1000} step={1} value={target} disabled={busy || uncertain} onChange={(e) => setTarget(e.target.value)} /></label>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-app-muted">Market entry · attached SL/TP</p>
          <div className="grid grid-cols-2 gap-2">
            <Button size="sm" className="w-full" aria-label="Buy market + SL/TP" disabled={Boolean(blocked)} onClick={() => submit("BUY")}>Buy market</Button>
            <Button size="sm" className="w-full" variant="danger" aria-label="Sell market + SL/TP" disabled={Boolean(blocked)} onClick={() => submit("SELL")}>Sell market</Button>
          </div>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-app-muted">Account controls</p>
          <div className="grid grid-cols-2 gap-2">
            <Button size="sm" className="w-full" variant="secondary" aria-label="Refresh broker status" disabled={demoMode || refreshing || busy} onClick={() => void refresh()}>{refreshing ? "Refreshing…" : "Refresh"}</Button>
            <Button size="sm" className="w-full" variant="secondary" aria-label="Close positions & cancel orders" title="Close all positions and cancel all working orders on this account" disabled={demoMode || busy || flattening} onClick={onFlatten}>{flattening ? "Closing…" : "Flatten account"}</Button>
          </div>
        </div>
      </div>
      {snapshot && valid ? <p className="text-xs text-app-muted">{snapshot.contract.name || snapshot.contract.id} · SL {(sl * snapshot.contract.tick_size).toFixed(2)} pts / ${risk.toFixed(2)} · TP {(tp * snapshot.contract.tick_size).toFixed(2)} pts / ${(size * tp * snapshot.contract.tick_value).toFixed(2)}</p> : null}
      {disabledReason ? <p role="status" className="text-sm text-app-warning">{disabledReason}</p> : null}
      {hasExposure ? <p className="text-sm text-app-warning">A position or working order is already open. Finish this test before sending another entry.</p> : null}
      {risk > (snapshot?.max_stop_risk ?? 250) ? <p role="alert">Reduce contracts or stop distance to stay within the test limit.</p> : null}
      {error ? <p role="alert" className="text-sm text-app-danger">{error}</p> : null}
      {uncertain || unresolved ? <div role="alert" className="space-y-2 text-sm text-app-warning">
        <p>The last submission is not confirmed. Check TopstepX and refresh broker status before placing another order.</p>
        {uncertain && recoveryRequest.current ? <Button variant="secondary" disabled={busy || demoMode} onClick={() => void send(recoveryRequest.current!)}>Recover same submission</Button> : null}
      </div> : null}
      {result ? <p role="status" className={`text-sm${result.status === "error" ? " text-app-danger" : ""}`}>{result.side} {result.quantity} · {orderStatus(result)}{fillDetails(result)}{result.provider_order_id ? ` · TopstepX order ${result.provider_order_id}` : ""}{result.message ? ` · ${result.message}` : ""}{result.status === "submitted" && !result.execution_status ? ". Accepted by TopstepX; fill not yet confirmed." : ""}</p> : null}
      <div className="grid items-start gap-2 border-t border-app-border pt-2 text-xs text-app-muted sm:grid-cols-2">
        <details className="min-w-0 rounded-lg border border-app-border px-3 py-2" open={hasExposure || uncertain || unresolved || undefined}>
          <summary className="cursor-pointer font-medium">Broker details{snapshot ? ` · ${snapshot.positions.length} positions · ${snapshot.orders.length} orders · ${snapshot.recent_attempts.length} tests` : " · awaiting status"}</summary>
      {snapshot ? <div className="mt-2 grid gap-3 text-xs md:grid-cols-2">
        <div><h3 className="mb-2 font-semibold">Open positions</h3>{snapshot.positions.length ? snapshot.positions.map((p) => <p key={p.id}>{p.type === 1 ? "Long" : "Short"} {p.size} · {p.contract_id} · average fill {p.average_price ?? "pending"}</p>) : <p>No open positions.</p>}</div>
        <div><h3 className="mb-2 font-semibold">Working orders / brackets</h3>{snapshot.orders.length ? snapshot.orders.map((o) => <p key={o.order_id}>#{o.order_id} · {o.side === 0 ? "Buy" : "Sell"} {o.size} · {o.order_type === 4 ? "Stop" : o.order_type === 1 ? "Limit / TP" : `Type ${o.order_type}`} {o.stop_price ?? o.limit_price ?? ""} · {o.contract_id}</p>) : <p>No working orders.</p>}</div>
        <div className="md:col-span-2"><h3 className="mb-2 font-semibold">Recent manual tests</h3>{snapshot.recent_attempts.length ? snapshot.recent_attempts.map((a) => <p key={a.attempt_id}>{a.side} {a.quantity} · SL {a.stop_loss_ticks} / TP {a.take_profit_ticks} ticks · {orderStatus(a)}{fillDetails(a)}{a.provider_order_id ? ` · #${a.provider_order_id}` : ""}{a.message ? ` · ${a.message}` : ""}</p>) : <p>No test orders submitted.</p>}</div>
      </div> : null}
        </details>
        <details className="min-w-0 rounded-lg border border-app-border px-3 py-2">
          <summary className="cursor-pointer font-medium">Setup &amp; limits</summary>
          <div className="mt-2 space-y-2">
            <p>In TopstepX, enable Settings → Risk Settings → <strong>Auto OCO Brackets</strong>. SL/TP attach to the entry and use its fill price.</p>
            <p>Stop TopBot before testing. New entries require no open positions or working orders. Flatten account closes all positions and cancels all orders on the selected account.</p>
            <p>Stop-risk limit: ${snapshot?.max_stop_risk ?? 250}. Estimates exclude fees and slippage.</p>
          </div>
        </details>
      </div>
    </CardContent>
  </Card>;
}
