import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/Table";
import { ACCOUNT_QUERY_PARAM, parseAccountId } from "../../lib/accountSelection";
import { accountsApi, botsApi } from "../../lib/api";
import type {
  AccountInfo,
  BotActivity,
  BotConfig,
  BotEvaluation,
  BotTimeframeUnit,
  ProjectXContract,
  ProjectXMarketCandle,
} from "../../lib/types";

const timeframeUnits: BotTimeframeUnit[] = ["second", "minute", "hour", "day", "week", "month"];

const dateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  timeZone: "UTC",
});

interface BotFormState {
  name: string;
  accountId: string;
  contractSearch: string;
  contractId: string;
  symbol: string;
  timeframeUnit: BotTimeframeUnit;
  timeframeUnitNumber: string;
  lookbackBars: string;
  fastPeriod: string;
  slowPeriod: string;
  orderSize: string;
  maxContracts: string;
  maxDailyLoss: string;
  maxTradesPerDay: string;
  maxOpenPosition: string;
  tradingStartTime: string;
  tradingEndTime: string;
  cooldownSeconds: string;
  maxDataStalenessSeconds: string;
}

function buildInitialForm(accountId: number | null): BotFormState {
  return {
    name: "MNQ SMA Cross",
    accountId: accountId ? String(accountId) : "",
    contractSearch: "MNQ",
    contractId: "",
    symbol: "MNQ",
    timeframeUnit: "minute",
    timeframeUnitNumber: "5",
    lookbackBars: "200",
    fastPeriod: "9",
    slowPeriod: "21",
    orderSize: "1",
    maxContracts: "1",
    maxDailyLoss: "250",
    maxTradesPerDay: "3",
    maxOpenPosition: "1",
    tradingStartTime: "09:30",
    tradingEndTime: "15:45",
    cooldownSeconds: "300",
    maxDataStalenessSeconds: "600",
  };
}

function parsePositiveNumber(value: string) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function parseNonNegativeNumber(value: string) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function parsePositiveInt(value: string) {
  const parsed = Number.parseInt(value, 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function parseNonNegativeInt(value: string) {
  const parsed = Number.parseInt(value, 10);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
}

function formatDateTime(value: string | null) {
  if (!value) {
    return "None";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unknown";
  }
  return `${dateTimeFormatter.format(date)} UTC`;
}

function actionBadgeVariant(action: string) {
  if (action === "BUY") {
    return "positive" as const;
  }
  if (action === "SELL" || action === "STOP") {
    return "negative" as const;
  }
  return "neutral" as const;
}

function statusBadgeVariant(status: string) {
  if (status === "running" || status === "dry_run" || status === "submitted") {
    return "positive" as const;
  }
  if (status === "blocked" || status === "error" || status === "rejected") {
    return "negative" as const;
  }
  return "neutral" as const;
}

function Sparkline({ candles }: { candles: ProjectXMarketCandle[] }) {
  const closes = candles.map((candle) => candle.close).filter((value) => Number.isFinite(value));
  const path = useMemo(() => {
    if (closes.length < 2) {
      return "";
    }
    const min = Math.min(...closes);
    const max = Math.max(...closes);
    const span = max - min || 1;
    return closes
      .map((value, index) => {
        const x = (index / (closes.length - 1)) * 100;
        const y = 36 - ((value - min) / span) * 32;
        return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
      })
      .join(" ");
  }, [closes]);

  return (
    <svg viewBox="0 0 100 40" className="h-16 w-full overflow-visible" aria-hidden="true">
      <path d="M 0 38 L 100 38" stroke="rgba(148,163,184,0.25)" strokeWidth="1" />
      {path ? <path d={path} fill="none" stroke="rgb(34,211,238)" strokeWidth="2" vectorEffect="non-scaling-stroke" /> : null}
    </svg>
  );
}

export function BotPage() {
  const [searchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));
  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [configs, setConfigs] = useState<BotConfig[]>([]);
  const [selectedBotId, setSelectedBotId] = useState<number | null>(null);
  const [activity, setActivity] = useState<BotActivity | null>(null);
  const [lastEvaluation, setLastEvaluation] = useState<BotEvaluation | null>(null);
  const [contracts, setContracts] = useState<ProjectXContract[]>([]);
  const [form, setForm] = useState<BotFormState>(() => buildInitialForm(accountFromQuery));
  const [loading, setLoading] = useState(true);
  const [activityLoading, setActivityLoading] = useState(false);
  const [contractLoading, setContractLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const selectedBot = useMemo(
    () => configs.find((config) => config.id === selectedBotId) ?? configs[0] ?? null,
    [configs, selectedBotId],
  );

  const loadConfigs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [accountRows, botRows] = await Promise.all([
        accountsApi.getSelectableAccounts(),
        botsApi.listConfigs(accountFromQuery ?? undefined),
      ]);
      setAccounts(accountRows);
      setConfigs(botRows.items);
      setSelectedBotId((current) => {
        if (current && botRows.items.some((item) => item.id === current)) {
          return current;
        }
        return botRows.items[0]?.id ?? null;
      });
      if (accountRows.length > 0) {
        setForm((current) =>
          current.accountId ? current : { ...current, accountId: String(accountFromQuery ?? accountRows[0].id) },
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load bot data");
    } finally {
      setLoading(false);
    }
  }, [accountFromQuery]);

  const loadActivity = useCallback(async (botId: number | null) => {
    if (!botId) {
      setActivity(null);
      return;
    }
    setActivityLoading(true);
    try {
      const payload = await botsApi.getActivity(botId);
      setActivity(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load bot activity");
    } finally {
      setActivityLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConfigs();
  }, [loadConfigs]);

  useEffect(() => {
    void loadActivity(selectedBot?.id ?? null);
  }, [loadActivity, selectedBot?.id]);

  async function handleSearchContracts() {
    if (!form.contractSearch.trim()) {
      setFormError("Contract search is required.");
      return;
    }
    setContractLoading(true);
    setFormError(null);
    try {
      const rows = await botsApi.searchContracts({ searchText: form.contractSearch, live: false });
      setContracts(rows);
      if (rows[0]) {
        applyContract(rows[0]);
      }
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Contract search failed");
    } finally {
      setContractLoading(false);
    }
  }

  function applyContract(contract: ProjectXContract) {
    setForm((current) => ({
      ...current,
      contractId: contract.id,
      symbol: contract.symbol_id ?? contract.name,
      contractSearch: contract.name,
    }));
  }

  async function handleCreateBot(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const accountId = parsePositiveInt(form.accountId);
    const timeframeUnitNumber = parsePositiveInt(form.timeframeUnitNumber);
    const lookbackBars = parsePositiveInt(form.lookbackBars);
    const fastPeriod = parsePositiveInt(form.fastPeriod);
    const slowPeriod = parsePositiveInt(form.slowPeriod);
    const orderSize = parsePositiveNumber(form.orderSize);
    const maxContracts = parsePositiveNumber(form.maxContracts);
    const maxDailyLoss = parseNonNegativeNumber(form.maxDailyLoss);
    const maxTradesPerDay = parseNonNegativeInt(form.maxTradesPerDay);
    const maxOpenPosition = parsePositiveNumber(form.maxOpenPosition);
    const cooldownSeconds = parseNonNegativeInt(form.cooldownSeconds);
    const maxDataStalenessSeconds = parsePositiveInt(form.maxDataStalenessSeconds);

    if (
      accountId === null ||
      timeframeUnitNumber === null ||
      lookbackBars === null ||
      fastPeriod === null ||
      slowPeriod === null ||
      orderSize === null ||
      maxContracts === null ||
      maxDailyLoss === null ||
      maxTradesPerDay === null ||
      maxOpenPosition === null ||
      cooldownSeconds === null ||
      maxDataStalenessSeconds === null
    ) {
      setFormError("Numeric settings must be valid positive values.");
      return;
    }
    if (!form.contractId.trim()) {
      setFormError("Select a contract before saving.");
      return;
    }
    if (slowPeriod <= fastPeriod) {
      setFormError("Slow period must be greater than fast period.");
      return;
    }

    setSaving(true);
    setFormError(null);
    try {
      const created = await botsApi.createConfig({
        name: form.name,
        account_id: accountId,
        contract_id: form.contractId,
        symbol: form.symbol || null,
        enabled: false,
        execution_mode: "dry_run",
        strategy_type: "sma_cross",
        timeframe_unit: form.timeframeUnit,
        timeframe_unit_number: timeframeUnitNumber,
        lookback_bars: lookbackBars,
        fast_period: fastPeriod,
        slow_period: slowPeriod,
        order_size: orderSize,
        max_contracts: maxContracts,
        max_daily_loss: maxDailyLoss,
        max_trades_per_day: maxTradesPerDay,
        max_open_position: maxOpenPosition,
        allowed_contracts: [form.contractId],
        trading_start_time: form.tradingStartTime,
        trading_end_time: form.tradingEndTime,
        cooldown_seconds: cooldownSeconds,
        max_data_staleness_seconds: maxDataStalenessSeconds,
        allow_market_depth: false,
      });
      setSelectedBotId(created.id);
      await loadConfigs();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save bot");
    } finally {
      setSaving(false);
    }
  }

  async function runBotAction(kind: "start" | "evaluate" | "stop") {
    if (!selectedBot) {
      return;
    }
    setActionLoading(kind);
    setError(null);
    try {
      if (kind === "start") {
        const result = await botsApi.start(selectedBot.id, { dryRun: true });
        setLastEvaluation(result);
      } else if (kind === "evaluate") {
        const result = await botsApi.evaluate(selectedBot.id, { dryRun: true });
        setLastEvaluation(result);
      } else {
        await botsApi.stop(selectedBot.id);
      }
      await Promise.all([loadConfigs(), loadActivity(selectedBot.id)]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bot action failed");
    } finally {
      setActionLoading(null);
    }
  }

  if (loading) {
    return (
      <div className="grid gap-5 lg:grid-cols-[1fr_1.4fr]">
        <Skeleton className="h-[520px]" />
        <Skeleton className="h-[520px]" />
      </div>
    );
  }

  return (
    <div className="space-y-5 pb-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-50">Bot</h1>
          <p className="mt-1 text-sm text-slate-400">ProjectX rule execution</p>
        </div>
        {selectedBot ? (
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={selectedBot.enabled ? "positive" : "neutral"}>
              {selectedBot.enabled ? "Enabled" : "Disabled"}
            </Badge>
            <Badge variant="accent">{selectedBot.execution_mode === "dry_run" ? "Dry run" : "Live"}</Badge>
          </div>
        ) : null}
      </div>

      {error ? <div className="rounded-xl border border-rose-400/35 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">{error}</div> : null}

      <div className="grid gap-5 xl:grid-cols-[420px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Configuration</CardTitle>
            <CardDescription>SMA cross, ProjectX candles, server-side audit trail</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={handleCreateBot}>
              <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                <span>Name</span>
                <Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
              </label>
              <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                <span>Account</span>
                <Select value={form.accountId} onChange={(event) => setForm({ ...form, accountId: event.target.value })}>
                  <option value="">Select account</option>
                  {accounts.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.name} ({account.id})
                    </option>
                  ))}
                </Select>
              </label>

              <div className="space-y-2">
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Contract</span>
                  <div className="flex gap-2">
                    <Input
                      value={form.contractSearch}
                      onChange={(event) => setForm({ ...form, contractSearch: event.target.value })}
                    />
                    <Button type="button" variant="secondary" onClick={handleSearchContracts} disabled={contractLoading}>
                      {contractLoading ? "Searching" : "Search"}
                    </Button>
                  </div>
                </label>
                {contracts.length > 0 ? (
                  <div className="grid gap-2">
                    {contracts.slice(0, 4).map((contract) => (
                      <button
                        key={contract.id}
                        type="button"
                        onClick={() => applyContract(contract)}
                        className="rounded-xl border border-slate-800 bg-slate-950/50 px-3 py-2 text-left text-xs text-slate-300 transition hover:border-cyan-400/45"
                      >
                        <span className="font-semibold text-slate-100">{contract.name}</span>
                        <span className="ml-2 text-slate-500">{contract.id}</span>
                      </button>
                    ))}
                  </div>
                ) : null}
                {form.contractId ? <p className="text-xs text-slate-500">{form.contractId}</p> : null}
              </div>

              <div className="grid grid-cols-3 gap-3">
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Unit</span>
                  <Select
                    value={form.timeframeUnit}
                    onChange={(event) => setForm({ ...form, timeframeUnit: event.target.value as BotTimeframeUnit })}
                  >
                    {timeframeUnits.map((unit) => (
                      <option key={unit} value={unit}>
                        {unit}
                      </option>
                    ))}
                  </Select>
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Size</span>
                  <Input value={form.timeframeUnitNumber} onChange={(event) => setForm({ ...form, timeframeUnitNumber: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Bars</span>
                  <Input value={form.lookbackBars} onChange={(event) => setForm({ ...form, lookbackBars: event.target.value })} />
                </label>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Fast SMA</span>
                  <Input value={form.fastPeriod} onChange={(event) => setForm({ ...form, fastPeriod: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Slow SMA</span>
                  <Input value={form.slowPeriod} onChange={(event) => setForm({ ...form, slowPeriod: event.target.value })} />
                </label>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Order Size</span>
                  <Input value={form.orderSize} onChange={(event) => setForm({ ...form, orderSize: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Max Contracts</span>
                  <Input value={form.maxContracts} onChange={(event) => setForm({ ...form, maxContracts: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Daily Loss</span>
                  <Input value={form.maxDailyLoss} onChange={(event) => setForm({ ...form, maxDailyLoss: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Max Open</span>
                  <Input
                    value={form.maxOpenPosition}
                    onChange={(event) => setForm({ ...form, maxOpenPosition: event.target.value })}
                  />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Trades/Day</span>
                  <Input value={form.maxTradesPerDay} onChange={(event) => setForm({ ...form, maxTradesPerDay: event.target.value })} />
                </label>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Start ET</span>
                  <Input value={form.tradingStartTime} onChange={(event) => setForm({ ...form, tradingStartTime: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>End ET</span>
                  <Input value={form.tradingEndTime} onChange={(event) => setForm({ ...form, tradingEndTime: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Cooldown</span>
                  <Input value={form.cooldownSeconds} onChange={(event) => setForm({ ...form, cooldownSeconds: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Stale Sec</span>
                  <Input
                    value={form.maxDataStalenessSeconds}
                    onChange={(event) => setForm({ ...form, maxDataStalenessSeconds: event.target.value })}
                  />
                </label>
              </div>

              {formError ? <p className="text-sm text-rose-300">{formError}</p> : null}
              <Button className="w-full" type="submit" disabled={saving}>
                {saving ? "Saving" : "Save Bot"}
              </Button>
            </form>
          </CardContent>
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
              <div>
                <CardTitle>Deployment</CardTitle>
                <CardDescription>Dry-run deployment and audit status</CardDescription>
              </div>
              <Select
                className="md:max-w-sm"
                value={selectedBot?.id ? String(selectedBot.id) : ""}
                onChange={(event) => setSelectedBotId(Number.parseInt(event.target.value, 10))}
              >
                {configs.length === 0 ? <option value="">No bots</option> : null}
                {configs.map((config) => (
                  <option key={config.id} value={config.id}>
                    {config.name}
                  </option>
                ))}
              </Select>
            </CardHeader>
            <CardContent>
              {selectedBot ? (
                <div className="space-y-4">
                  <div className="grid gap-3 md:grid-cols-4">
                    <Metric label="Account" value={String(selectedBot.account_id)} />
                    <Metric label="Contract" value={selectedBot.symbol ?? selectedBot.contract_id} />
                    <Metric label="SMA" value={`${selectedBot.fast_period}/${selectedBot.slow_period}`} />
                    <Metric label="Risk" value={`$${selectedBot.max_daily_loss.toFixed(0)}`} />
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button onClick={() => void runBotAction("start")} disabled={actionLoading !== null}>
                      {actionLoading === "start" ? "Starting" : "Start Dry Run"}
                    </Button>
                    <Button variant="secondary" onClick={() => void runBotAction("evaluate")} disabled={actionLoading !== null}>
                      {actionLoading === "evaluate" ? "Evaluating" : "Evaluate"}
                    </Button>
                    <Button variant="danger" onClick={() => void runBotAction("stop")} disabled={actionLoading !== null}>
                      {actionLoading === "stop" ? "Stopping" : "Stop"}
                    </Button>
                  </div>
                  {lastEvaluation ? (
                    <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
                      <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <Badge variant={actionBadgeVariant(lastEvaluation.decision.action)}>
                            {lastEvaluation.decision.action}
                          </Badge>
                          <span className="text-xs text-slate-500">{formatDateTime(lastEvaluation.decision.candle_timestamp)}</span>
                        </div>
                        <p className="text-sm text-slate-200">{lastEvaluation.decision.reason}</p>
                        {lastEvaluation.order_attempt ? (
                          <p className="mt-2 text-xs text-slate-400">
                            Order attempt #{lastEvaluation.order_attempt.id}: {lastEvaluation.order_attempt.status}
                          </p>
                        ) : null}
                        {lastEvaluation.risk_events.length > 0 ? (
                          <div className="mt-3 space-y-1">
                            {lastEvaluation.risk_events.map((risk) => (
                              <p key={risk.id} className="text-xs text-amber-200">
                                {risk.code}: {risk.message}
                              </p>
                            ))}
                          </div>
                        ) : null}
                      </div>
                      <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
                        <Sparkline candles={lastEvaluation.candles} />
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : (
                <p className="text-sm text-slate-400">No bot configuration saved.</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Activity</CardTitle>
              <CardDescription>Signals, risk events, and order attempts</CardDescription>
            </CardHeader>
            <CardContent>
              {activityLoading ? (
                <Skeleton className="h-64" />
              ) : activity ? (
                <div className="grid gap-4 xl:grid-cols-2">
                  <ActivityTable
                    title="Decisions"
                    rows={activity.decisions.slice(0, 8).map((decision) => ({
                      id: decision.id,
                      left: decision.action,
                      middle: decision.reason,
                      right: formatDateTime(decision.created_at),
                      badgeVariant: actionBadgeVariant(decision.action),
                    }))}
                  />
                  <ActivityTable
                    title="Orders"
                    rows={activity.order_attempts.slice(0, 8).map((attempt) => ({
                      id: attempt.id,
                      left: attempt.status,
                      middle: `${attempt.side} ${attempt.size} ${attempt.contract_id}`,
                      right: formatDateTime(attempt.created_at),
                      badgeVariant: statusBadgeVariant(attempt.status),
                    }))}
                  />
                  <ActivityTable
                    title="Risk"
                    rows={activity.risk_events.slice(0, 8).map((risk) => ({
                      id: risk.id,
                      left: risk.severity,
                      middle: `${risk.code}: ${risk.message}`,
                      right: formatDateTime(risk.created_at),
                      badgeVariant: risk.severity === "critical" ? "negative" : "warning",
                    }))}
                  />
                  <ActivityTable
                    title="Runs"
                    rows={activity.runs.slice(0, 8).map((run) => ({
                      id: run.id,
                      left: run.status,
                      middle: run.stop_reason ?? (run.dry_run ? "dry_run" : "live"),
                      right: formatDateTime(run.started_at),
                      badgeVariant: statusBadgeVariant(run.status),
                    }))}
                  />
                </div>
              ) : (
                <p className="text-sm text-slate-400">No activity.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-slate-100">{value}</p>
    </div>
  );
}

interface ActivityRow {
  id: number;
  left: string;
  middle: string;
  right: string;
  badgeVariant: "positive" | "negative" | "neutral" | "accent" | "warning";
}

function ActivityTable({ title, rows }: { title: string; rows: ActivityRow[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-800">
      <div className="border-b border-slate-800 bg-slate-900/50 px-3 py-2 text-sm font-semibold text-slate-100">{title}</div>
      {rows.length === 0 ? (
        <p className="px-3 py-4 text-sm text-slate-500">No rows</p>
      ) : (
        <div className="max-h-64 overflow-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-24">State</TableHead>
                <TableHead>Detail</TableHead>
                <TableHead className="w-32 text-right">Time</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  <TableCell>
                    <Badge variant={row.badgeVariant}>{row.left}</Badge>
                  </TableCell>
                  <TableCell className="max-w-[320px] truncate text-xs text-slate-300">{row.middle}</TableCell>
                  <TableCell className="text-right text-xs text-slate-500">{row.right}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
